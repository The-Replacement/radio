"""Regression checks for the mainframe receive hook."""

from __future__ import annotations

from pathlib import Path


def test_post_receive_does_not_rewrite_origin_tracking_refs() -> None:
    hook = Path(__file__).resolve().parents[1] / "scripts" / "post-receive"
    commands = (
        line.strip()
        for line in hook.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )

    assert not any("update-ref" in line and "refs/remotes/origin/" in line for line in commands), (
        "a deploy push must not fabricate synchronization with GitHub"
    )
