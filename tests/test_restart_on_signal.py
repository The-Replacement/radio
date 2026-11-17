"""Regression checks for scripts/restart-on-signal.sh (#127).

The handler must not consume the rotation signal when `docker compose restart
liquidsoap` fails: the marker is written only on success, so the next tick
retries instead of silently losing the rotation. Both outcomes are logged.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HANDLER = REPO / "scripts" / "restart-on-signal.sh"

SIGNAL = "1794909154.9108682"


def _log_text(sandbox: Path) -> str:
    log = sandbox / "data" / "restart-on-signal.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """A repo-shaped sandbox: the handler, the sourced watchdog lib, a radio-job
    stub that closes the gate (so no real render/docker compose run), and the
    signal file."""
    root = tmp_path / "radio"
    (root / "scripts" / "lib").mkdir(parents=True)
    (root / "data" / "indiegen").mkdir(parents=True)
    shutil.copy(HANDLER, root / "scripts" / "restart-on-signal.sh")
    shutil.copy(
        REPO / "scripts" / "lib" / "render-watchdog.sh",
        root / "scripts" / "lib" / "render-watchdog.sh",
    )
    # Gate closed: the render is optional by design, so the handler goes
    # straight to the restart we want to test.
    radio_job = root / "scripts" / "radio-job.sh"
    radio_job.write_text(
        "#!/usr/bin/env bash\necho 'gate closed: test gate'\nexit 1\n",
        encoding="utf-8",
    )
    radio_job.chmod(0o755)
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "data" / "indiegen" / "restart-request").write_text(SIGNAL, encoding="utf-8")
    return root


def _docker_stub(bin_dir: Path) -> Path:
    """PATH-shim docker: records every invocation, honours DOCKER_RC, prints a
    line so the handler's output capture is exercised."""
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$DOCKER_CALLS"\n'
        'echo "Container radio-liquidsoap restarting..."\n'
        'exit "${DOCKER_RC:-0}"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _run(sandbox: Path, bin_dir: Path, docker_rc: int) -> subprocess.CompletedProcess[str]:
    calls = sandbox / "docker-calls.log"
    env = {
        **os.environ,
        "PATH": os.pathsep.join([str(bin_dir), os.environ["PATH"]]),
        "DOCKER_RC": str(docker_rc),
        "DOCKER_CALLS": str(calls),
    }
    return subprocess.run(
        ["bash", str(sandbox / "scripts" / "restart-on-signal.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _docker_calls(sandbox: Path) -> list[str]:
    calls = sandbox / "docker-calls.log"
    return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def test_failed_restart_does_not_consume_the_signal(sandbox: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _docker_stub(bin_dir)

    first = _run(sandbox, bin_dir, docker_rc=1)
    assert first.returncode == 1, first.stdout + first.stderr

    marker = sandbox / "data" / "indiegen" / ".last-restart"
    assert not marker.exists(), "failed restart must not write the marker"

    log = _log_text(sandbox)
    assert "WARN restart failed (rc=1)" in log
    assert "NOT consumed" in log
    assert f"signal {SIGNAL}" in log

    # The signal survives, so the next tick retries the restart.
    second = _run(sandbox, bin_dir, docker_rc=1)
    assert second.returncode == 1
    assert len(_docker_calls(sandbox)) == 2, "next invocation must retry the restart"
    assert _log_text(sandbox).count("WARN restart failed") == 2


def test_successful_restart_consumes_the_signal(sandbox: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _docker_stub(bin_dir)

    first = _run(sandbox, bin_dir, docker_rc=0)
    assert first.returncode == 0, first.stdout + first.stderr

    marker = sandbox / "data" / "indiegen" / ".last-restart"
    assert marker.read_text(encoding="utf-8") == SIGNAL

    log = _log_text(sandbox)
    assert f"restart ok: signal {SIGNAL}" in log
    # docker's own output lands in the log, not in cron's mail spool.
    assert "restart: Container radio-liquidsoap restarting..." in log

    # Idempotent: the same signal must not restart again.
    second = _run(sandbox, bin_dir, docker_rc=0)
    assert second.returncode == 0
    assert len(_docker_calls(sandbox)) == 1, "consumed signal must not restart twice"


def test_render_lock_holder_message_names_any_holder(sandbox: Path, tmp_path: Path) -> None:
    """The shared render lock is held by hourly refresh OR a slot job; the
    message must not blame the hourly (#127 secondary defect)."""
    bin_dir = tmp_path / "bin"
    _docker_stub(bin_dir)

    lock_path = Path("/tmp/mario-refresh.lock")
    try:
        handle = lock_path.open("w")
    except OSError as exc:  # pragma: no cover - exotic /tmp
        pytest.skip(f"cannot open render lock: {exc}")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pytest.skip("render lock already held by a real render")
        result = _run(sandbox, bin_dir, docker_rc=0)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        handle.close()

    log = _log_text(sandbox)
    assert "render lock held by another job" in log
    assert "hourly refresh is rendering" not in log
    # The restart still happens while the render is skipped.
    assert f"restart ok: signal {SIGNAL}" in log
