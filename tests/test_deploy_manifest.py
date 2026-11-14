"""Deployment evidence: the manifest the receive hook writes, and its verifier.

The manifest is the record the radio lacked: which source commit produced
which image, what the hook that ran hashed to, and what a rollback would
target. These tests pin the behaviour that makes it evidence rather than
decoration: atomic writes inside the gitignored data directory, a rollback
target, and a verifier that fails when the box and the manifest disagree.

The integration tests at the end run the real receive hook against a stub
docker, then verify the result: the wiring between hook, build and manifest is
the part that breaks silently.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "deploy_manifest.py"
HOOK_PATH = REPO_ROOT / "scripts" / "post-receive"

HOOK_BODY = "#!/bin/sh\n# the tracked receive hook\necho hook\n"
SOURCE_SHA = "a" * 40
PREVIOUS_SHA = "b" * 40
NEXT_SHA = "c" * 40
MARIO_IMAGE_ID = "sha256:" + "1" * 64
CAM_IMAGE_ID = "sha256:" + "2" * 64
OTHER_IMAGE_ID = "sha256:" + "3" * 64


def _load_module() -> Any:
    """Load the standalone script as a module, the way the hook runs it."""
    spec = importlib.util.spec_from_file_location("deploy_manifest", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve the module of a class through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy_manifest = _load_module()


class FakeRunner:
    """Answers commands by substring, in the order the patterns were added.

    Tests add the specific patterns first: an answer for ``radio-mario`` must be
    reached before the generic one for ``radio-cam``.
    """

    def __init__(self) -> None:
        self.responses: list[tuple[str, Any]] = []
        self.calls: list[tuple[str, ...]] = []

    def on(self, pattern: str, returncode: int = 0, stdout: bytes = b"") -> FakeRunner:
        self.responses.append(
            (pattern, deploy_manifest.CommandResult(returncode, stdout, b""))
        )
        return self

    def __call__(self, argv: Any, cwd: Any = None) -> Any:
        args = tuple(str(part) for part in argv)
        self.calls.append(args)
        joined = " ".join(args)
        for pattern, result in self.responses:
            if pattern in joined:
                return result
        raise AssertionError(f"the fake runner has no answer for: {joined}")


def _compose_config() -> bytes:
    return json.dumps(
        {
            "name": "radio",
            "services": {
                "mario": {"container_name": "radio-mario"},
                "cam": {"container_name": "radio-cam"},
            },
        }
    ).encode()


def _image_inspect(image_id: str, revision: str) -> bytes:
    return json.dumps(
        {
            "Id": image_id,
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": revision,
                    "org.opencontainers.image.source": (
                        "https://github.com/The-Replacement/radio"
                    ),
                }
            },
        }
    ).encode()


def _make_root(tmp_path: Path) -> Path:
    """A repository-shaped directory with a tracked hook and a hook symlink."""
    root = tmp_path / "radio"
    (root / "scripts").mkdir(parents=True)
    (root / ".githooks").mkdir()
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "scripts" / "post-receive").write_text(HOOK_BODY, encoding="utf-8")
    (root / ".githooks" / "post-receive").symlink_to(Path("../scripts/post-receive"))
    return root


def _recording_runner(
    *, mario_revision: str = SOURCE_SHA, cam_revision: str = SOURCE_SHA
) -> FakeRunner:
    """A runner that answers everything the recorder asks, consistently."""
    runner = FakeRunner()
    runner.on("radio-mario", stdout=_image_inspect(MARIO_IMAGE_ID, mario_revision))
    runner.on("radio-cam", stdout=_image_inspect(CAM_IMAGE_ID, cam_revision))
    runner.on("config --format json", stdout=_compose_config())
    runner.on("rev-parse --git-path hooks/post-receive", stdout=b".githooks/post-receive")
    runner.on("diff --name-only", stdout=b"src/mario/cli.py\nDockerfile.mario\n")
    return runner


def _record(root: Path, runner: FakeRunner, **kwargs: Any) -> dict[str, Any]:
    """Write a manifest with the given overrides."""
    arguments: dict[str, Any] = {
        "root": root,
        "ref": "refs/heads/master",
        "old_sha": PREVIOUS_SHA,
        "new_sha": SOURCE_SHA,
        "reason": "push to master",
        "rebuilt": ["mario"],
        "runner": runner,
    }
    arguments.update(kwargs)
    _, payload = deploy_manifest.record_manifest(**arguments)
    return cast(dict[str, Any], payload)


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------


def test_record_writes_the_manifest_inside_the_gitignored_data_directory(
    tmp_path: Path,
) -> None:
    root = _make_root(tmp_path)
    payload = _record(root, _recording_runner())

    manifest = root / "data" / "deploy-manifest.json"
    assert manifest.is_file()
    assert json.loads(manifest.read_text(encoding="utf-8")) == payload
    assert payload["source_sha"] == SOURCE_SHA
    assert payload["status"] == "deployed"
    assert payload["change"]["key"] == f"{PREVIOUS_SHA}..{SOURCE_SHA}"
    assert payload["change"]["changed_paths"] == ["src/mario/cli.py", "Dockerfile.mario"]


def test_record_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _record(root, _recording_runner())

    leftovers = list((root / "data").glob("*.tmp"))
    assert leftovers == [], "the manifest is written with a temp file that must be renamed"


def test_record_never_touches_the_tracked_tree(tmp_path: Path) -> None:
    root = _make_root(tmp_path)

    def tracked_files() -> list[Path]:
        return sorted(
            path.relative_to(root)
            for path in root.rglob("*")
            if path.is_file() and "data" not in path.relative_to(root).parts
        )

    before = tracked_files()
    _record(root, _recording_runner())

    assert tracked_files() == before, (
        "the manifest belongs to the gitignored data directory, not to the tree"
    )


def test_record_captures_the_hook_that_ran_and_the_image_revisions(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    payload = _record(root, _recording_runner())

    hook = payload["hook"]
    assert hook["path"] == ".githooks/post-receive"
    assert hook["resolved_path"] == "scripts/post-receive"
    assert hook["sha256"] == hashlib.sha256(HOOK_BODY.encode()).hexdigest()
    assert payload["images"]["mario"]["image_id"] == MARIO_IMAGE_ID
    assert payload["images"]["mario"]["revision"] == SOURCE_SHA
    assert payload["images"]["mario"]["rebuilt"] is True
    assert payload["images"]["cam"]["image_id"] == CAM_IMAGE_ID
    assert payload["images"]["cam"]["rebuilt"] is False


def test_record_keeps_the_previous_manifest_as_the_rollback_target(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _record(root, _recording_runner())

    second = _record(root, _recording_runner(), new_sha=NEXT_SHA)

    previous = second["previous"]
    assert previous is not None
    assert previous["source_sha"] == SOURCE_SHA
    assert previous["images"]["mario"]["image_id"] == MARIO_IMAGE_ID
    assert previous["images"]["cam"]["image_id"] == CAM_IMAGE_ID
    assert previous["change"]["key"] == f"{PREVIOUS_SHA}..{SOURCE_SHA}"
    assert "previous" not in previous, (
        "the chain is one level deep: a rollback needs a target, not a history"
    )


def test_record_of_a_failed_deploy_says_so(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    payload = _record(root, _recording_runner(), status="failed", reason="mario build failed")

    assert payload["status"] == "failed"
    assert payload["change"]["reason"] == "mario build failed"


# --------------------------------------------------------------------------
# The verifier
# --------------------------------------------------------------------------

FETCH_REFLOG = f"{SOURCE_SHA}\x1forigin/master@{{2026-11-14 15:00:00 -0300}}\x1ffetch: storing head"


def _manifest_with(root: Path, **overrides: Any) -> None:
    """A manifest on disk that agrees with the fake box."""
    payload: dict[str, Any] = {
        "schema_version": 1,
        "deployed_at": "2026-11-14T20:31:05Z",
        "status": "deployed",
        "source_sha": SOURCE_SHA,
        "ref": "refs/heads/master",
        "change": {
            "key": f"{PREVIOUS_SHA}..{SOURCE_SHA}",
            "old_sha": PREVIOUS_SHA,
            "new_sha": SOURCE_SHA,
            "reason": "push to master",
            "changed_paths": ["src/mario/cli.py"],
        },
        "hook": {
            "path": ".githooks/post-receive",
            "resolved_path": "scripts/post-receive",
            "sha256": hashlib.sha256(HOOK_BODY.encode()).hexdigest(),
        },
        "images": {
            "mario": {
                "image": "radio-mario",
                "container": "radio-mario",
                "resolved_by": "compose_config",
                "image_id": MARIO_IMAGE_ID,
                "revision": SOURCE_SHA,
                "source": "https://github.com/The-Replacement/radio",
                "rebuilt": True,
                "error": None,
            },
            "cam": {
                "image": "radio-cam",
                "container": "radio-cam",
                "resolved_by": "compose_config",
                "image_id": CAM_IMAGE_ID,
                "revision": SOURCE_SHA,
                "source": "https://github.com/The-Replacement/radio",
                "rebuilt": False,
                "error": None,
            },
        },
        "previous": None,
    }
    payload.update(overrides)
    path = root / "data" / "deploy-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _verifying_runner() -> FakeRunner:
    """A runner that answers everything the verifier asks, consistently."""
    runner = FakeRunner()
    runner.on("rev-parse --git-path hooks/post-receive", stdout=b".githooks/post-receive")
    runner.on("rev-parse HEAD", stdout=SOURCE_SHA.encode())
    runner.on("cat-file blob", stdout=HOOK_BODY.encode())
    runner.on("rev-parse --verify --quiet", stdout=SOURCE_SHA.encode())
    runner.on("reflog show", stdout=FETCH_REFLOG.encode())
    runner.on("merge-base --is-ancestor")
    runner.on("ls-remote", stdout=f"{SOURCE_SHA}\trefs/heads/master".encode())
    runner.on("{{json .}} radio-mario", stdout=_image_inspect(MARIO_IMAGE_ID, SOURCE_SHA))
    runner.on("{{json .}} radio-cam", stdout=_image_inspect(CAM_IMAGE_ID, SOURCE_SHA))
    runner.on("inspect --format {{.Image}}|{{.State.Running}} radio-mario", stdout=f"{MARIO_IMAGE_ID}|true".encode())
    runner.on("inspect --format {{.Image}}|{{.State.Running}} radio-cam", stdout=f"{CAM_IMAGE_ID}|true".encode())
    return runner


def _check(checks: list[Any], name: str) -> Any:
    matching = [check for check in checks if check.name == name]
    assert matching, f"no check named {name}"
    return matching[0]


def test_verify_passes_when_the_box_agrees_with_the_manifest(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)

    checks = deploy_manifest.verify(root, _verifying_runner())

    statuses = [check.status for check in checks]
    assert "FAIL" not in statuses
    assert "UNKNOWN" not in statuses
    text, code = deploy_manifest.verdict(checks)
    assert code == 0
    assert text.startswith("OK")


def test_verify_reports_a_mismatch_when_the_checkout_moved(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(0, ("rev-parse HEAD", deploy_manifest.CommandResult(0, b"d" * 40, b"")))

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "checkout").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_reports_a_mismatch_when_the_hook_is_not_the_tracked_script(
    tmp_path: Path,
) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    (root / "scripts" / "post-receive").write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")

    checks = deploy_manifest.verify(root, _verifying_runner())

    assert _check(checks, "hook").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_reports_a_mismatch_when_an_image_is_not_the_recorded_one(
    tmp_path: Path,
) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(
        0, ("{{json .}} radio-cam", deploy_manifest.CommandResult(
            0, _image_inspect(OTHER_IMAGE_ID, SOURCE_SHA), b""
        ))
    )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "image:cam").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_reports_a_mismatch_when_a_running_container_is_on_another_image(
    tmp_path: Path,
) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(
        0, ("inspect --format {{.Image}}|{{.State.Running}} radio-mario", deploy_manifest.CommandResult(
            0, f"{CAM_IMAGE_ID}|true".encode(), b""
        ))
    )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "container:mario").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_does_not_fail_a_stopped_one_off_container(tmp_path: Path) -> None:
    """mario and cam run on demand; a stopped container is not drift."""
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(
        0, ("inspect --format {{.Image}}|{{.State.Running}} radio-mario", deploy_manifest.CommandResult(
            0, f"{CAM_IMAGE_ID}|false".encode(), b""
        ))
    )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "container:mario").status == "NOTE"
    assert not [c for c in checks if c.status == "FAIL"
                and c.name.startswith("container:")]


def test_verify_reports_a_mismatch_when_a_rebuilt_image_carries_another_revision(
    tmp_path: Path,
) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(
        0, ("{{json .}} radio-mario", deploy_manifest.CommandResult(
            0, _image_inspect(MARIO_IMAGE_ID, "e" * 40), b""
        ))
    )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "label:mario").status == "FAIL"
    assert _check(checks, "revision:mario").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_reports_a_mismatch_when_the_tracking_ref_was_written_locally(
    tmp_path: Path,
) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(
        0,
        (
            "reflog show",
            deploy_manifest.CommandResult(
                0, f"{SOURCE_SHA}\x1forigin/master@{{2026-11-14 20:31:00 +0000}}\x1f".encode(), b""
            ),
        ),
    )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "tracking-ref").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_reports_a_mismatch_when_the_tracking_ref_moved_during_the_deploy(
    tmp_path: Path,
) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(
        0,
        (
            "reflog show",
            deploy_manifest.CommandResult(
                0,
                f"{SOURCE_SHA}\x1forigin/master@{{2026-11-14 20:31:00 +0000}}"
                f"\x1freset: moving to HEAD".encode(),
                b"",
            ),
        ),
    )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "tracking-ref").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_accepts_a_push_to_github_inside_the_deploy_window(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(
        0,
        (
            "reflog show",
            deploy_manifest.CommandResult(
                0,
                f"{SOURCE_SHA}\x1forigin/master@{{2026-11-14 20:31:00 +0000}}"
                f"\x1fupdate by push".encode(),
                b"",
            ),
        ),
    )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "tracking-ref").status == "PASS"


def test_verify_ignores_a_local_write_that_the_ref_has_since_outgrown(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    runner.responses.insert(
        0,
        (
            "reflog show",
            deploy_manifest.CommandResult(
                0,
                (
                    f"{SOURCE_SHA}\x1forigin/master@{{2026-11-14 15:00:00 -0300}}"
                    f"\x1ffetch: storing head\n"
                    f"{PREVIOUS_SHA}\x1forigin/master@{{2026-11-01 10:00:00 -0300}}\x1f"
                ).encode(),
                b"",
            ),
        ),
    )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "tracking-ref").status == "PASS"


def test_verify_reports_a_mismatch_when_the_last_deploy_failed(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root, status="failed")

    checks = deploy_manifest.verify(root, _verifying_runner())

    assert _check(checks, "deploy-status").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_without_a_manifest_is_a_mismatch(tmp_path: Path) -> None:
    root = _make_root(tmp_path)

    checks = deploy_manifest.verify(root, _verifying_runner())

    assert _check(checks, "manifest").status == "FAIL"
    _, code = deploy_manifest.verdict(checks)
    assert code == 1


def test_verify_says_unverified_when_the_box_cannot_be_inspected(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _manifest_with(root)
    runner = _verifying_runner()
    for pattern in (
        "{{json .}} radio-mario",
        "{{json .}} radio-cam",
        "inspect --format {{.Image}}|{{.State.Running}} radio-mario",
        "inspect --format {{.Image}}|{{.State.Running}} radio-cam",
    ):
        runner.responses.insert(
            0, (pattern, deploy_manifest.CommandResult(1, b"", b"no docker daemon"))
        )

    checks = deploy_manifest.verify(root, runner)

    assert _check(checks, "image:mario").status == "UNKNOWN"
    text, code = deploy_manifest.verdict(checks)
    assert code == 2
    assert text.startswith("UNVERIFIED")


# --------------------------------------------------------------------------
# The wiring: hook, build and manifest
# --------------------------------------------------------------------------

STUB_DOCKER = """#!/usr/bin/env python3
\"\"\"A docker that only knows what the receive hook asks of it.\"\"\"
import json
import os
import sys

argv = sys.argv[1:]
if argv[:1] == ["compose"] and "config" in argv:
    print(json.dumps({"name": "radio", "services": {
        "mario": {"container_name": "radio-mario"},
        "cam": {"container_name": "radio-cam"}}}))
elif argv[:2] == ["image", "inspect"]:
    print(json.dumps({"Id": "sha256:" + "1" * 64, "Config": {"Labels": {
        "org.opencontainers.image.revision": os.environ.get("VCS_REF", ""),
        "org.opencontainers.image.source": "https://github.com/The-Replacement/radio"}}}))
elif argv[:1] == ["inspect"]:
    print("sha256:" + "1" * 64)
else:
    sys.exit(0)
"""


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _deployed_repo(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """A repo where the tracked hook already deployed a second commit."""
    root = tmp_path / "radio"
    (root / "scripts").mkdir(parents=True)
    (root / ".githooks").mkdir()
    (root / "src" / "mario").mkdir(parents=True)

    _git(tmp_path, "init", "-q", "-b", "master", str(root))
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "core.hooksPath", ".githooks")

    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "src" / "mario" / "cli.py").write_text("print('one')\n", encoding="utf-8")
    (root / "scripts" / "post-receive").write_text(
        HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "scripts" / "deploy_manifest.py").write_text(
        MODULE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / ".githooks" / "post-receive").symlink_to(Path("../scripts/post-receive"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "first")
    old_sha = _git(root, "rev-parse", "HEAD")

    (root / "src" / "mario" / "cli.py").write_text("print('two')\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "second")
    new_sha = _git(root, "rev-parse", "HEAD")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB_DOCKER, encoding="utf-8")
    stub.chmod(0o755)
    return root, bin_dir, old_sha, new_sha


def _path_with(bin_dir: Path) -> str:
    """PATH with the stub docker first and the test interpreter's python3."""
    return os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), os.environ["PATH"]])


def test_the_receive_hook_writes_the_manifest_of_the_deploy(tmp_path: Path) -> None:
    root, bin_dir, old_sha, new_sha = _deployed_repo(tmp_path)

    completed = subprocess.run(
        ["sh", str(root / "scripts" / "post-receive")],
        cwd=root / ".git",
        input=f"{old_sha} {new_sha} refs/heads/master\n",
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": _path_with(bin_dir)},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    manifest = json.loads((root / "data" / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_sha"] == new_sha
    assert manifest["status"] == "deployed"
    assert manifest["images"]["mario"]["rebuilt"] is True
    assert manifest["images"]["cam"]["rebuilt"] is False
    # The image carries the pushed SHA, because the hook exports it before the
    # build: the revision of a running image is the commit that produced it.
    assert manifest["images"]["mario"]["revision"] == new_sha
    assert manifest["hook"]["resolved_path"] == "scripts/post-receive"
    assert manifest["hook"]["sha256"] == hashlib.sha256(
        (root / "scripts" / "post-receive").read_bytes()
    ).hexdigest()


def test_the_deployed_manifest_verifies_against_the_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, bin_dir, old_sha, new_sha = _deployed_repo(tmp_path)
    env = {**os.environ, "PATH": _path_with(bin_dir)}
    subprocess.run(
        ["sh", str(root / "scripts" / "post-receive")],
        cwd=root / ".git",
        input=f"{old_sha} {new_sha} refs/heads/master\n",
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    # The remote-tracking ref came from a fetch: the reflog subject says so.
    _git(root, "update-ref", "-m", "fetch origin: fast-forward", "refs/remotes/origin/master", new_sha)

    # The stub image answers with the label it was built with.
    monkeypatch.setenv("PATH", _path_with(bin_dir))
    monkeypatch.setenv("VCS_REF", new_sha)

    checks = deploy_manifest.verify(root, deploy_manifest.subprocess_runner, online=False)

    statuses = [check.status for check in checks]
    assert "FAIL" not in statuses, [check.detail for check in checks if check.status == "FAIL"]
    assert "UNKNOWN" not in statuses
    text, code = deploy_manifest.verdict(checks)
    assert code == 0, text


def test_the_hook_and_the_images_carry_the_oci_revision() -> None:
    hook = HOOK_PATH.read_text(encoding="utf-8")
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'VCS_REF="$NEW"' in hook, "the hook must tell the build which commit it is"
    assert "export VCS_REF" in hook
    assert "manifest_write deployed" in hook
    assert "deploy_manifest.py" in hook
    assert "VCS_REF:" in compose, "the compose build must pass the revision to the image"
    for dockerfile in (REPO_ROOT / "Dockerfile.mario", REPO_ROOT / "cam" / "Dockerfile"):
        text = dockerfile.read_text(encoding="utf-8")
        assert "org.opencontainers.image.revision" in text
        assert "org.opencontainers.image.source" in text
        assert 'ARG VCS_REF=""' in text


def test_the_manifest_never_lands_in_the_tracked_tree() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert any(line.strip() == "data/" for line in ignored), (
        "the manifest lives in data/, so data/ must stay gitignored"
    )


@pytest.mark.parametrize("script", ["scripts/deploy_manifest.py"])
def test_the_manifest_script_is_executable(script: str) -> None:
    assert os.access(REPO_ROOT / script, os.X_OK), f"{script} runs from the hook: it needs +x"
