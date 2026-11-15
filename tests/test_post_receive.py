"""Regression checks for the mainframe receive hook."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "scripts" / "post-receive"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True, check=True)


def _log_line(target: Path, needle: str) -> bool:
    log = target / "data" / "deploy.log"
    if not log.exists():
        return False
    return any(needle in line for line in log.read_text(encoding="utf-8").splitlines())


@pytest.fixture()
def deployment(tmp_path: Path) -> tuple[Path, Path]:
    """A sandbox shaped like the mainframe: a bare hub, a seed clone and a
    worktree clone with ``updateInstead`` and the real hook installed."""
    bare = tmp_path / "hub.git"
    seed = tmp_path / "seed"
    target = tmp_path / "servers" / "radio"
    subprocess.run(
        ("git", "init", "--bare", "-b", "master", str(bare)), capture_output=True, check=True
    )
    _git("clone", str(bare), str(seed), cwd=tmp_path)
    _git("config", "user.email", "test@example.test", cwd=seed)
    _git("config", "user.name", "post-receive test", cwd=seed)
    (seed / "README.md").write_text("# radio sandbox\n", encoding="utf-8")
    (seed / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    scripts = seed / "scripts"
    scripts.mkdir()
    shutil.copy(REPO / "scripts" / "deploy_manifest.py", scripts / "deploy_manifest.py")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "seed", cwd=seed)
    _git("push", "origin", "master", cwd=seed)
    _git("clone", str(bare), str(target), cwd=tmp_path)
    _git("config", "receive.denyCurrentBranch", "updateInstead", cwd=target)
    hooks = target / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    shutil.copy(HOOK, hooks / "post-receive")
    (hooks / "post-receive").chmod(0o755)
    return seed, target


def test_post_receive_does_not_rewrite_origin_tracking_refs() -> None:
    commands = (
        line.strip()
        for line in HOOK.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )

    assert not any("update-ref" in line and "refs/remotes/origin/" in line for line in commands), (
        "a deploy push must not fabricate synchronization with GitHub"
    )


def test_push_to_wrong_branch_fails_the_hook_and_names_checked_out_branch(
    deployment: tuple[Path, Path],
) -> None:
    """infra#12: updateInstead only moves the worktree for the checked-out
    branch; a push to any other branch must not build or log success."""
    seed, target = deployment
    _git("checkout", "-b", "feature", cwd=seed)
    (seed / "README.md").write_text("# feature\n", encoding="utf-8")
    _git("commit", "-am", "feature", cwd=seed)
    _git("push", "origin", "feature", cwd=seed)
    # The ref itself lands (a post-receive failure cannot un-land it); the
    # deploy must not happen and the checked-out branch must be named.
    pushed = subprocess.run(
        ("git", "push", str(target), "feature"),
        cwd=seed,
        capture_output=True,
        text=True,
        check=False,
    )
    assert _log_line(target, "FAIL: pushed refs/heads/feature but HEAD is on refs/heads/master")
    assert not _log_line(target, "deploy ready")
    assert not _log_line(target, "worktree moved to")
    worktree_sha = _git("rev-parse", "HEAD", cwd=target).stdout.strip()
    feature_sha = _git("rev-parse", "feature", cwd=seed).stdout.strip()
    assert worktree_sha != feature_sha, "the worktree must not move for a foreign branch"
    assert pushed.returncode == 0  # git semantics: the ref update already happened


def test_push_to_checked_out_branch_deploys_and_logs_worktree_commit(
    deployment: tuple[Path, Path],
) -> None:
    seed, target = deployment
    (seed / "README.md").write_text("# second\n", encoding="utf-8")
    _git("commit", "-am", "second", cwd=seed)
    _git("push", "origin", "master", cwd=seed)
    pushed = subprocess.run(
        ("git", "push", str(target), "master"),
        cwd=seed,
        capture_output=True,
        text=True,
        check=False,
    )
    assert pushed.returncode == 0
    new_sha = _git("rev-parse", "master", cwd=seed).stdout.strip()
    assert _log_line(target, f"worktree moved to {new_sha}")
    assert _log_line(target, "deploy ready")
    assert _git("rev-parse", "HEAD", cwd=target).stdout.strip() == new_sha


def test_push_to_detached_head_fails_loudly(deployment: tuple[Path, Path]) -> None:
    """#107: a detached HEAD cannot be moved by updateInstead; the hook must
    say so instead of building stale code under a success line."""
    seed, target = deployment
    _git("checkout", "--detach", cwd=target)
    (seed / "README.md").write_text("# third\n", encoding="utf-8")
    _git("commit", "-am", "third", cwd=seed)
    _git("push", "origin", "master", cwd=seed)
    subprocess.run(
        ("git", "push", str(target), "master"),
        cwd=seed,
        capture_output=True,
        text=True,
        check=False,
    )
    assert _log_line(target, "FAIL: HEAD is detached")
    assert not _log_line(target, "deploy ready")


def test_tag_push_is_not_a_deploy(deployment: tuple[Path, Path]) -> None:
    seed, target = deployment
    _git("tag", "v0-test", cwd=seed)
    _git("push", "origin", "v0-test", cwd=seed)
    subprocess.run(
        ("git", "push", str(target), "v0-test"),
        cwd=seed,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not _log_line(target, "push received")
    assert not _log_line(target, "deploy ready")
