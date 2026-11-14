#!/usr/bin/env python3
"""Deployment evidence for the radio: the manifest writer and its verifier.

WHY
---
The receive hook (``scripts/post-receive``) used to leave exactly one trace: a
line in ``data/deploy.log``. A log line is a claim, not evidence. Nothing
recorded which source commit produced which image, so the only answer to "what
is running?" was "the checkout is at SHA X" -- and a checkout says nothing
about the artifacts a container was created from. A push could rebuild an
image from stale code, or the box could keep serving an image built weeks ago,
and the log would read the same either way.

This module writes the missing record -- a JSON manifest, one per deploy,
inside the gitignored ``data/`` directory -- and verifies the box against it.

It is deliberately standalone: standard library only, no project imports, so
the receive hook can run it on the box with nothing but ``python3``.

COMMANDS
--------
``record``
    Writes the manifest atomically (temp file + rename), so a reader never
    sees half a document.

``verify``
    Read-only. Answers, with evidence:
      1. does the running container image match the manifest?
      2. does the manifest SHA match the deployed checkout?
      3. does the hook hash match the tracked script?
      4. is the local remote-tracking ref truthful (not rewritten by a deploy)?

    Exit codes: 0 everything agrees, 1 something disagrees, 2 something could
    not be evaluated (no docker, no network, no manifest to read).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "deploy-manifest.json"
DATA_DIRNAME = "data"
COMPOSE_FILE = "docker-compose.yml"

DEPLOYED = "deployed"
FAILED = "failed"

REVISION_LABEL = "org.opencontainers.image.revision"
SOURCE_LABEL = "org.opencontainers.image.source"
REPO_SOURCE_URL = "https://github.com/The-Replacement/radio"

TRACKED_HOOK = "scripts/post-receive"
TRACKING_REMOTE = "origin"
TRACKING_BRANCH = "master"

MANAGED_SERVICES = ("mario", "cam")

# A deploy is a moment, not an instant: the manifest is written at its end and
# the reflog entry of a rewritten tracking ref could land a few seconds either
# side. Fifteen minutes is wide enough for the clock and narrow enough that a
# fetch an hour later is not mistaken for the deploy.
DEPLOY_WINDOW = timedelta(minutes=15)

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
NOTE = "NOTE"


# --------------------------------------------------------------------------
# Running external commands
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    """The result of one external command."""

    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        """stdout as text, without ever raising on invalid bytes."""
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def error(self) -> str:
        """stderr as text, trimmed."""
        return self.stderr.decode("utf-8", errors="replace").strip()


class CommandRunner(Protocol):
    """Runs an external command. Tests substitute their own."""

    def __call__(self, argv: Sequence[str], cwd: Path | None = None) -> CommandResult: ...


def subprocess_runner(argv: Sequence[str], cwd: Path | None = None) -> CommandResult:
    """Run a command and capture its output.

    A missing binary is a failed command, not an exception: the caller asks
    questions, and "docker is not installed" is an answer, not a crash.
    """
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(returncode=127, stdout=b"", stderr=str(exc).encode())
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or b"",
        stderr=completed.stderr or b"",
    )


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """The hex sha256 of some bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    """The hex sha256 of a file, or None when it cannot be read."""
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


def read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON object, or None when it is missing or unusable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON so a reader sees either the old file or the new one, never half.

    The temp file lives in the same directory (same filesystem, so the rename
    is atomic), the file is fsynced before the rename and the directory after
    it: without that last fsync a crash can lose the rename and leave the
    manifest pointing at the previous deploy.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _relative_to_root(path: Path, root: Path) -> str:
    """A path relative to the repository root when it is inside it."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _slug(name: str) -> str:
    """Compose's project-name rules, approximately: lowercase, no funny chars."""
    cleaned = "".join(char if char.isalnum() or char in "_-" else "-" for char in name.lower())
    return cleaned.strip("-") or "radio"


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------


def resolve_hook(root: Path, runner: CommandRunner) -> dict[str, Any]:
    """Where git looks for the receive hook, and the file behind it.

    ``git rev-parse --git-path hooks/post-receive`` honours core.hooksPath, so
    it answers the question that matters: the file git actually runs. The path
    is then resolved through symlinks -- this repo keeps .githooks/post-receive
    as a symlink to scripts/post-receive -- and hashed, so the manifest records
    the hook that ran, not the name of a file that resembles it.
    """
    res = runner(["git", "rev-parse", "--git-path", "hooks/post-receive"], cwd=root)
    raw = res.text.strip()
    if res.returncode != 0 or not raw:
        return {"path": None, "resolved_path": None, "sha256": None}
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    return {
        "path": _relative_to_root(candidate, root),
        "resolved_path": _relative_to_root(resolved, root),
        "sha256": sha256_file(resolved),
    }


def tracked_hook_digest(root: Path, runner: CommandRunner, sha: str) -> str | None:
    """The sha256 of ``scripts/post-receive`` as committed at ``sha``.

    Read from the object store, not from the worktree: the worktree can be
    dirty, and the question is what the deployed commit contains.
    """
    res = runner(["git", "cat-file", "blob", f"{sha}:{TRACKED_HOOK}"], cwd=root)
    if res.returncode != 0:
        return None
    return sha256_bytes(res.stdout)


def head_sha(root: Path, runner: CommandRunner) -> str | None:
    """The commit the checkout is at, or None when git cannot say."""
    res = runner(["git", "rev-parse", "HEAD"], cwd=root)
    if res.returncode != 0:
        return None
    return res.text.strip() or None


def changed_paths(root: Path, runner: CommandRunner, old_sha: str, new_sha: str) -> list[str]:
    """The paths that changed between two commits."""
    res = runner(["git", "diff", "--name-only", old_sha, new_sha], cwd=root)
    if res.returncode != 0:
        return []
    return [line for line in res.text.splitlines() if line]


def is_ancestor(root: Path, runner: CommandRunner, older: str, newer: str) -> bool:
    """True when ``older`` is an ancestor of ``newer``."""
    res = runner(["git", "merge-base", "--is-ancestor", older, newer], cwd=root)
    return res.returncode == 0


@dataclass(frozen=True)
class ReflogEntry:
    """One movement of a ref."""

    sha: str
    action: str
    when: datetime | None


def read_reflog(root: Path, runner: CommandRunner, ref: str) -> list[ReflogEntry]:
    """The reflog of a ref, newest first.

    The subject of an entry is the evidence: a ref that moves by fetch, pull,
    clone or by a push to that remote says so; a bare ``git update-ref``
    leaves the subject empty. An empty subject is a local write, and a local
    write is a claim, not a synchronization.
    """
    res = runner(
        ["git", "reflog", "show", "--date=iso", "--format=%H%x1f%gd%x1f%gs", ref],
        cwd=root,
    )
    if res.returncode != 0:
        return []
    entries: list[ReflogEntry] = []
    for line in res.text.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        sha, selector, action = parts[0], parts[1], parts[2]
        entries.append(ReflogEntry(sha=sha, action=action.strip(), when=_parse_reflog_date(selector)))
    return entries


def _parse_reflog_date(selector: str) -> datetime | None:
    """The timestamp inside a reflog selector such as ``origin/master@{...}``."""
    start = selector.find("{")
    end = selector.find("}")
    if start == -1 or end == -1 or end <= start:
        return None
    stamp = selector[start + 1 : end]
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def remote_head_sha(root: Path, runner: CommandRunner, remote: str, branch: str) -> str | None:
    """The commit the remote actually has on a branch, or None when unreachable."""
    res = runner(["git", "ls-remote", remote, f"refs/heads/{branch}"], cwd=root)
    if res.returncode != 0:
        return None
    fields = res.text.split()
    if len(fields) < 2:
        return None
    return fields[0]


# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceLayout:
    """Where a service's image and container live."""

    image: str
    container: str | None
    resolved_by: str


def compose_layout(root: Path, runner: CommandRunner) -> dict[str, ServiceLayout]:
    """Resolve the image and container names of the compose services.

    The compose file is the single source of truth: ``docker compose config
    --format json`` answers the project name and the container names, and the
    image of a service that only declares ``build:`` is ``<project>-<service>``.
    When docker is not reachable the names are guessed from the project
    directory, and the manifest records which of the two happened so nobody
    mistakes a guess for a fact.
    """
    project = ""
    services: dict[str, dict[str, Any]] = {}
    compose_file = root / COMPOSE_FILE
    res = runner(
        ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"],
        cwd=root,
    )
    if res.returncode == 0:
        try:
            config = json.loads(res.text)
        except ValueError:
            config = {}
        if isinstance(config, dict):
            name = config.get("name")
            project = name if isinstance(name, str) else ""
            raw_services = config.get("services")
            if isinstance(raw_services, dict):
                for service, body in raw_services.items():
                    if isinstance(body, dict):
                        services[str(service)] = body
    if not project:
        project = os.environ.get("COMPOSE_PROJECT_NAME") or _slug(root.name)

    layout: dict[str, ServiceLayout] = {}
    for service in MANAGED_SERVICES:
        body = services.get(service, {})
        image = body.get("image")
        container = body.get("container_name")
        container_name = container if isinstance(container, str) and container else None
        if isinstance(image, str) and image:
            layout[service] = ServiceLayout(
                image=image, container=container_name, resolved_by="compose_config"
            )
        else:
            layout[service] = ServiceLayout(
                image=f"{project}-{service}", container=container_name, resolved_by="fallback"
            )
    return layout


def inspect_image(runner: CommandRunner, image: str) -> dict[str, Any]:
    """The id and the OCI labels of a local image."""
    empty: dict[str, Any] = {
        "image_id": None,
        "revision": None,
        "source": None,
        "error": None,
    }
    res = runner(["docker", "image", "inspect", "--format", "{{json .}}", image])
    if res.returncode != 0:
        empty["error"] = res.error[:200] or "docker could not inspect the image"
        return empty
    try:
        payload = json.loads(res.text.strip().splitlines()[0])
    except (ValueError, IndexError):
        empty["error"] = "unreadable docker inspect output"
        return empty
    if not isinstance(payload, dict):
        empty["error"] = "unreadable docker inspect output"
        return empty
    config = payload.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        labels = {}
    image_id = payload.get("Id")
    revision = labels.get(REVISION_LABEL)
    source = labels.get(SOURCE_LABEL)
    return {
        "image_id": image_id if isinstance(image_id, str) else None,
        "revision": revision if isinstance(revision, str) else None,
        "source": source if isinstance(source, str) else None,
        "error": None,
    }


def container_image_id(runner: CommandRunner, container: str) -> str | None:
    """The image id a container was created from, or None when it has none."""
    res = runner(["docker", "inspect", "--format", "{{.Image}}", container])
    if res.returncode != 0:
        return None
    return res.text.strip() or None


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------


def manifest_path(root: Path) -> Path:
    """Where the manifest lives: inside the gitignored data directory."""
    return root / DATA_DIRNAME / MANIFEST_FILENAME


def _previous_summary(previous: dict[str, Any] | None) -> dict[str, Any] | None:
    """The previous manifest, without its own chain.

    A rollback needs a target: the previous commit and the image ids that
    commit produced. One level is enough; an unbounded chain would grow the
    file forever and buy nothing.
    """
    if previous is None:
        return None
    return {key: value for key, value in previous.items() if key != "previous"}


def build_manifest(
    *,
    root: Path,
    ref: str,
    old_sha: str,
    new_sha: str,
    reason: str,
    rebuilt: Sequence[str],
    runner: CommandRunner,
    now: datetime | None = None,
    status: str = DEPLOYED,
) -> dict[str, Any]:
    """Assemble the manifest of one deploy.

    Everything here is read-only: the manifest records what the box has, it
    never changes it.
    """
    timestamp = now or datetime.now(UTC)
    layout = compose_layout(root, runner)
    images: dict[str, Any] = {}
    for service in MANAGED_SERVICES:
        service_layout = layout[service]
        inspected = inspect_image(runner, service_layout.image)
        images[service] = {
            "image": service_layout.image,
            "container": service_layout.container,
            "resolved_by": service_layout.resolved_by,
            "image_id": inspected["image_id"],
            "revision": inspected["revision"],
            "source": inspected["source"],
            "rebuilt": service in rebuilt,
            "error": inspected["error"],
        }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "deployed_at": timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "source_sha": new_sha,
        "ref": ref,
        "change": {
            "key": f"{old_sha}..{new_sha}",
            "old_sha": old_sha,
            "new_sha": new_sha,
            "reason": reason,
            "changed_paths": changed_paths(root, runner, old_sha, new_sha),
        },
        "hook": resolve_hook(root, runner),
        "images": images,
        "previous": _previous_summary(read_json(manifest_path(root))),
    }


def record_manifest(
    *,
    root: Path,
    ref: str,
    old_sha: str,
    new_sha: str,
    reason: str,
    rebuilt: Sequence[str],
    runner: CommandRunner,
    now: datetime | None = None,
    status: str = DEPLOYED,
) -> tuple[Path, dict[str, Any]]:
    """Write the manifest of a deploy and return where it landed."""
    payload = build_manifest(
        root=root,
        ref=ref,
        old_sha=old_sha,
        new_sha=new_sha,
        reason=reason,
        rebuilt=rebuilt,
        runner=runner,
        now=now,
        status=status,
    )
    path = manifest_path(root)
    write_json_atomic(path, payload)
    return path, payload


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One verification result."""

    name: str
    status: str
    detail: str


def _check_deploy_status(manifest: Mapping[str, Any]) -> Check:
    """Did the deploy the manifest describes actually succeed?

    A failed deploy is recorded too: the push landed, the worktree moved and
    the artifacts did not. Leaving the previous manifest in place would claim
    the box runs a commit it does not run.
    """
    status = str(manifest.get("status") or DEPLOYED)
    reason = ""
    change = manifest.get("change")
    if isinstance(change, dict):
        reason = str(change.get("reason") or "")
    if status == FAILED:
        return Check("deploy-status", FAIL, f"the last deploy failed ({reason or 'no reason recorded'})")
    return Check("deploy-status", PASS, f"the last deploy succeeded ({reason or 'no reason recorded'})")


def _check_checkout(root: Path, runner: CommandRunner, source_sha: str) -> Check:
    """Does the checkout stand where the manifest says it does?"""
    head = head_sha(root, runner)
    if head is None:
        return Check("checkout", UNKNOWN, "git could not resolve HEAD")
    if head == source_sha:
        return Check("checkout", PASS, f"HEAD is {head}")
    return Check(
        "checkout",
        FAIL,
        f"HEAD is {head}, the manifest recorded {source_sha or '(none)'}",
    )


def _check_hook(
    root: Path, runner: CommandRunner, manifest: Mapping[str, Any], source_sha: str
) -> Check:
    """Does the hook that runs hash like the script the commit tracks?"""
    hook = manifest.get("hook")
    recorded = str(hook.get("sha256") or "") if isinstance(hook, dict) else ""
    live = resolve_hook(root, runner)
    live_digest = live.get("sha256")
    if not isinstance(live_digest, str):
        return Check("hook", UNKNOWN, "the receive hook could not be read")
    tracked = tracked_hook_digest(root, runner, source_sha) if source_sha else None
    if tracked is None:
        return Check("hook", UNKNOWN, f"{TRACKED_HOOK} is not in commit {source_sha[:12]}")
    if live_digest != tracked:
        return Check(
            "hook",
            FAIL,
            f"the running hook ({live.get('resolved_path')}) hashes {live_digest[:12]}, "
            f"{TRACKED_HOOK} at {source_sha[:12]} hashes {tracked[:12]}",
        )
    if recorded and recorded != live_digest:
        return Check(
            "hook",
            FAIL,
            f"the running hook hashes {live_digest[:12]}, the manifest recorded {recorded[:12]}",
        )
    return Check(
        "hook",
        PASS,
        f"{live.get('resolved_path')} hashes {live_digest[:12]}, like {TRACKED_HOOK} at {source_sha[:12]}",
    )


def _check_images(
    root: Path, runner: CommandRunner, manifest: Mapping[str, Any], source_sha: str
) -> list[Check]:
    """Are the images on the box the ones the manifest recorded?"""
    images = manifest.get("images")
    if not isinstance(images, dict) or not images:
        return [Check("images", UNKNOWN, "the manifest records no images")]
    checks: list[Check] = []
    for service in sorted(images):
        recorded = images[service]
        if not isinstance(recorded, dict):
            continue
        image = str(recorded.get("image") or "")
        inspected = inspect_image(runner, image)
        live_id = inspected["image_id"]
        if not isinstance(live_id, str):
            checks.append(
                Check(
                    f"image:{service}",
                    UNKNOWN,
                    f"{image} could not be inspected ({inspected['error']})",
                )
            )
            continue
        recorded_id = str(recorded.get("image_id") or "")
        if live_id == recorded_id:
            checks.append(Check(f"image:{service}", PASS, f"{image} is {live_id[:19]}"))
        else:
            checks.append(
                Check(
                    f"image:{service}",
                    FAIL,
                    f"{image} is {live_id[:19]}, the manifest recorded {recorded_id[:19] or '(none)'}",
                )
            )
        recorded_revision = str(recorded.get("revision") or "")
        live_revision = str(inspected["revision"] or "")
        if live_revision == recorded_revision:
            checks.append(
                Check(
                    f"label:{service}",
                    PASS,
                    f"{image} carries {REVISION_LABEL}={live_revision[:12] or '(empty)'}",
                )
            )
        else:
            checks.append(
                Check(
                    f"label:{service}",
                    FAIL,
                    f"{image} carries {REVISION_LABEL}={live_revision[:12] or '(empty)'}, "
                    f"the manifest recorded {recorded_revision[:12] or '(empty)'}",
                )
            )
        if recorded.get("rebuilt"):
            if live_revision == source_sha:
                checks.append(
                    Check(
                        f"revision:{service}",
                        PASS,
                        f"{image} was rebuilt by this deploy and carries {source_sha[:12]}",
                    )
                )
            else:
                checks.append(
                    Check(
                        f"revision:{service}",
                        FAIL,
                        f"{image} was rebuilt by this deploy but carries "
                        f"{live_revision[:12] or '(empty)'}, not {source_sha[:12]}",
                    )
                )
    return checks


def _check_containers(
    root: Path, runner: CommandRunner, manifest: Mapping[str, Any]
) -> list[Check]:
    """Do the running containers run the images the manifest recorded?"""
    images = manifest.get("images")
    if not isinstance(images, dict) or not images:
        return []
    checks: list[Check] = []
    for service in sorted(images):
        recorded = images[service]
        if not isinstance(recorded, dict):
            continue
        container = recorded.get("container")
        if not isinstance(container, str) or not container:
            checks.append(
                Check(f"container:{service}", UNKNOWN, "the manifest names no container")
            )
            continue
        res = runner(["docker", "inspect", "--format", "{{.Image}}", container])
        if res.returncode != 0:
            checks.append(
                Check(
                    f"container:{service}",
                    NOTE,
                    f"no container named {container}: nothing of this service is running",
                )
            )
            continue
        running = res.text.strip()
        recorded_id = str(recorded.get("image_id") or "")
        if running == recorded_id:
            checks.append(Check(f"container:{service}", PASS, f"{container} runs {running[:19]}"))
        else:
            checks.append(
                Check(
                    f"container:{service}",
                    FAIL,
                    f"{container} runs {running[:19] or '(none)'}, "
                    f"the manifest recorded {recorded_id[:19] or '(none)'}",
                )
            )
    return checks


def _check_tracking_ref(
    root: Path,
    runner: CommandRunner,
    manifest: Mapping[str, Any],
    source_sha: str,
    *,
    online: bool,
) -> Check:
    """Is the remote-tracking ref telling the truth?

    The failure this guards against is a deploy that rewrites
    refs/remotes/origin/* to make the box look synchronized with GitHub. The
    reflog is the evidence: a ref that moves by fetch, pull, clone or by a
    push to that remote says so in its subject; a bare ``git update-ref``
    leaves the subject empty. An empty subject is a local write, and a local
    write is a claim, not a synchronization.
    """
    ref = f"refs/remotes/{TRACKING_REMOTE}/{TRACKING_BRANCH}"
    res = runner(["git", "rev-parse", "--verify", "--quiet", ref], cwd=root)
    if res.returncode != 0:
        return Check("tracking-ref", UNKNOWN, f"{ref} does not exist: nothing to compare")
    local = res.text.strip()

    entries = read_reflog(root, runner, ref)
    if not entries:
        return Check(
            "tracking-ref",
            UNKNOWN,
            f"{ref} has no reflog: how it got there cannot be proven",
        )
    # The latest entry is the provenance of the current value. An empty subject
    # is a bare `git update-ref`: a local write, not a synchronization.
    if not entries[0].action:
        return Check(
            "tracking-ref",
            FAIL,
            f"{ref} was written locally (its latest reflog entry has no action): "
            "it does not come from the remote",
        )

    deployed_at = _parse_utc(manifest.get("deployed_at"))
    if deployed_at is not None:
        window_start = deployed_at - DEPLOY_WINDOW
        window_end = deployed_at + DEPLOY_WINDOW
        during = [
            entry
            for entry in entries
            if entry.when is not None and window_start <= entry.when <= window_end
        ]
        # A fetch, pull, clone or a push to that remote are real synchronization;
        # anything else inside the deploy window is a local rewrite.
        unjustified = [
            entry
            for entry in during
            if not entry.action.startswith(("fetch", "pull", "clone", "update by push"))
        ]
        if unjustified:
            return Check(
                "tracking-ref",
                FAIL,
                f"{ref} moved during the deploy window "
                f"({unjustified[0].action or '(no action)'}): a deploy does not synchronize GitHub",
            )

    if source_sha:
        behind = is_ancestor(root, runner, local, source_sha)
        ahead = is_ancestor(root, runner, source_sha, local)
        if not behind and not ahead:
            return Check(
                "tracking-ref",
                FAIL,
                f"{ref} ({local[:12]}) and the deployed commit ({source_sha[:12]}) "
                "are on disjoint histories: the ref was rewritten",
            )

    detail = f"{ref} is {local[:12]}, moved only by {entries[0].action or '(unknown)'}"
    if online:
        remote = remote_head_sha(root, runner, TRACKING_REMOTE, TRACKING_BRANCH)
        if remote is None:
            detail += "; the remote could not be reached"
        elif remote != local:
            detail += f"; the remote is at {remote[:12]} (the local ref is behind)"
    return Check("tracking-ref", PASS, detail)


def _parse_utc(value: Any) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp as written by this module."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def verify(root: Path, runner: CommandRunner, *, online: bool = True) -> list[Check]:
    """Check the box against the manifest. Read-only: nothing is written."""
    path = manifest_path(root)
    manifest = read_json(path)
    if manifest is None:
        return [Check("manifest", FAIL, f"no readable manifest at {path}")]

    source_sha = str(manifest.get("source_sha") or "")
    checks = [
        Check(
            "manifest",
            PASS,
            f"{path} (source {source_sha[:12] or '(none)'}, deployed {manifest.get('deployed_at')})",
        ),
        _check_deploy_status(manifest),
        _check_checkout(root, runner, source_sha),
        _check_hook(root, runner, manifest, source_sha),
    ]
    checks.extend(_check_images(root, runner, manifest, source_sha))
    checks.extend(_check_containers(root, runner, manifest))
    checks.append(_check_tracking_ref(root, runner, manifest, source_sha, online=online))
    return checks


def render_checks(checks: Sequence[Check]) -> str:
    """The human-readable report."""
    return "\n".join(f"  {check.status:<7} {check.name:<18} {check.detail}" for check in checks)


def verdict(checks: Sequence[Check]) -> tuple[str, int]:
    """The final word, and the exit code that goes with it."""
    failed = [check for check in checks if check.status == FAIL]
    unknown = [check for check in checks if check.status == UNKNOWN]
    if failed:
        return f"MISMATCH: {len(failed)} check(s) disagree with the manifest", 1
    if unknown:
        return f"UNVERIFIED: {len(unknown)} check(s) could not be evaluated", 2
    return "OK: every check agrees with the manifest", 0


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deploy_manifest.py",
        description="Write and verify the radio's deployment manifest.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="write the manifest of a deploy")
    record.add_argument("--root", default=".", help="repository root (default: .)")
    record.add_argument("--ref", required=True, help="the ref that was pushed")
    record.add_argument("--old", required=True, help="the commit that was replaced")
    record.add_argument("--new", required=True, help="the commit that was deployed")
    record.add_argument("--reason", default="", help="why the deploy happened")
    record.add_argument(
        "--status",
        choices=(DEPLOYED, FAILED),
        default=DEPLOYED,
        help="whether the deploy succeeded (a failed deploy is recorded too)",
    )
    record.add_argument(
        "--rebuilt",
        action="append",
        default=[],
        help="a service whose image this deploy rebuilt (repeatable, comma-separated)",
    )

    verify_parser = subparsers.add_parser(
        "verify", help="check the box against the manifest (read-only)"
    )
    verify_parser.add_argument("--root", default=".", help="repository root (default: .)")
    verify_parser.add_argument(
        "--offline", action="store_true", help="do not contact the remote"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    runner: CommandRunner = subprocess_runner

    if args.command == "record":
        rebuilt: list[str] = []
        for value in args.rebuilt:
            rebuilt.extend(part.strip() for part in value.split(",") if part.strip())
        try:
            path, payload = record_manifest(
                root=root,
                ref=args.ref,
                old_sha=args.old,
                new_sha=args.new,
                reason=args.reason,
                rebuilt=rebuilt,
                runner=runner,
                status=args.status,
            )
        except OSError as exc:
            print(f"deploy-manifest: the manifest could not be written: {exc}", file=sys.stderr)
            return 1
        print(
            f"deploy-manifest: written {path} "
            f"(status {payload['status']}, source {payload['source_sha'][:12]})"
        )
        return 0

    checks = verify(root, runner, online=not args.offline)
    print(f"Deployment evidence — {root}")
    print()
    print(render_checks(checks))
    print()
    text, code = verdict(checks)
    print(f"VERDICT: {text}")
    return code


if __name__ == "__main__":
    sys.exit(main())
