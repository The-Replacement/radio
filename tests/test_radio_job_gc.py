"""Behavioural gate for `radio-job gc` retention (#103).

The real invocation needs a docker daemon and a live box. This exercises the
decision logic against a stub `docker` instead: what gets pruned, in what order,
and whether the audit line accounts for the bytes.

The defect this pins: `docker builder prune --keep-storage` exits 0 having freed
0B on a box without buildx (measured on the mainframe 2026-11-18: 5.148GB
reclaimable, 0B reclaimed). The age prune is the mechanism that works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "radio-job.sh"

STUB = """#!/usr/bin/env bash
# Stub docker: answers `system df` from $STUB_DF, records prunes in $STUB_CALLS.
state="$STUB_DF"
calls="$STUB_CALLS"

case "$1" in
  info) exit 0 ;;
  system)
    # `docker system df --format '{{.Type}}|{{.Size}}'`
    cache=$(cat "$state")
    printf 'Type|Size\\nImages|1GB\\nContainers|10MB\\nLocal Volumes|1MB\\nBuild Cache|%s\\n' "$cache"
    exit 0 ;;
  builder)
    printf 'builder %s\\n' "$*" >> "$calls"
    if [ "$2" = "prune" ] && printf '%s' "$*" | grep -q -- '-af'; then
      echo 0 > "$state"
    fi
    echo "Total reclaimed space: 0B"
    exit 0 ;;
  image)
    printf 'image %s\\n' "$*" >> "$calls"
    echo "Total reclaimed space: 0B"
    exit 0 ;;
esac
exit 0
"""


@pytest.fixture()
def appliance(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A fake radio root with a stub docker on PATH."""
    root = tmp_path / "radio"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "radio-job.sh")
    (root / "data").mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(0o755)
    # Hermetic: no real passwordless sudo, no real journal.
    sudo = bin_dir / "sudo"
    sudo.write_text("#!/usr/bin/env bash\nexit 1\n")
    sudo.chmod(0o755)

    df_state = tmp_path / "cache-size"
    calls = tmp_path / "calls"
    df_state.write_text("0B")
    calls.write_text("")
    return root, df_state, calls


def _gc(
    root: Path, tmp_path: Path, df_state: Path, calls: Path, extra_env: dict | None = None
) -> str:
    calls.write_text("")
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "STUB_DF": str(df_state),
        "STUB_CALLS": str(calls),
        **(extra_env or {}),
    }
    subprocess.run(
        ["bash", str(root / "scripts" / "radio-job.sh"), "gc"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return (root / "data" / "gc.log").read_text()


def test_age_prune_is_used_because_keep_storage_is_not_enough(appliance, tmp_path) -> None:
    root, df_state, calls = appliance
    df_state.write_text("8.735GB")

    _gc(root, tmp_path, df_state, calls)

    issued = calls.read_text()
    assert "--keep-storage 5GB" in issued
    assert "--filter until=168h" in issued, "the age prune is the mechanism that reclaims"


def test_cache_above_the_cap_after_the_age_prune_is_emptied(appliance, tmp_path) -> None:
    root, df_state, calls = appliance
    df_state.write_text("8.735GB")  # age prune frees nothing in the stub

    log = _gc(root, tmp_path, df_state, calls)

    assert "pruned all" in log
    assert "-af" in calls.read_text()
    assert "build cache 8.735GB -> 0B (freed 8.735GB)" in log


def test_cache_below_the_cap_is_left_alone(appliance, tmp_path) -> None:
    root, df_state, calls = appliance
    df_state.write_text("3GB")

    log = _gc(root, tmp_path, df_state, calls)

    assert "-af" not in calls.read_text()
    assert "build cache 3GB -> 3GB (freed 0B)" in log


def test_second_run_frees_nothing(appliance, tmp_path) -> None:
    root, df_state, calls = appliance
    df_state.write_text("8.735GB")

    _gc(root, tmp_path, df_state, calls)
    log = _gc(root, tmp_path, df_state, calls)

    assert "build cache 0B -> 0B (freed 0B)" in log
    assert "gc done, freed 0B" in log


def test_log_accounts_for_each_class(appliance, tmp_path) -> None:
    root, df_state, calls = appliance
    df_state.write_text("8.735GB")

    log = _gc(root, tmp_path, df_state, calls)

    for line in ("build cache", "images", "journal", "gc done"):
        assert line in log, f"missing {line!r} in the audit log"


def test_dry_run_prunes_nothing(appliance, tmp_path) -> None:
    root, df_state, calls = appliance
    df_state.write_text("8.735GB")

    log = _gc(root, tmp_path, df_state, calls, extra_env={"RADIO_JOB_DRY_RUN": "1"})

    assert calls.read_text() == ""
    assert "dry: build cache is 8.735GB" in log
