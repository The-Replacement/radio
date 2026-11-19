"""Contract gate for `radio-job verify` → data/status.json (#104).

Pins fields the file must carry and that it is published under the door at
data/host/status.json. Uses stub docker/curl to run anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "radio-job.sh"
CGROUP_MEM = REPO_ROOT / "scripts" / "cgroup_mem.py"

STUB_DOCKER = r"""#!/usr/bin/env bash
# Minimal stub for radio-job verify.
if [ "$1" = "exec" ]; then
  case "$*" in
    *status-json.xsl*) printf '{"icestats":{"source":{"title":"Test Title"}}}'; exit 0 ;;
    *tx_bytes*) echo 1000; exit 0 ;;
    *"/proc/1"*) exit 0 ;;
  esac
  exit 0
fi
if [ "$1" = "inspect" ]; then
  # Formats vary: -f '{{.State.Running}}' radio-cam, etc.
  args="$*"
  if printf '%s' "$args" | grep -q 'State.Running'; then
    if printf '%s' "$args" | grep -q 'radio-cam'; then echo "false"; else echo "true"; fi
    exit 0
  fi
  if printf '%s' "$args" | grep -q 'HostConfig.Memory'; then
    # name mem restarts pid
    echo "radio-liquidsoap 0 0 123"
    exit 0
  fi
  echo ""
  exit 0
fi
if [ "$1" = "ps" ]; then
  exit 0
fi
if [ "$1" = "info" ]; then
  exit 0
fi
exit 0
"""

STUB_CURL = r"""#!/usr/bin/env bash
printf '404'
"""


@pytest.fixture()
def appliance(tmp_path: Path) -> Path:
    root = tmp_path / "radio"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "radio-job.sh")
    if CGROUP_MEM.exists():
        shutil.copy2(CGROUP_MEM, root / "scripts" / "cgroup_mem.py")
    else:
        # Minimal fallback: output empty pressure JSON
        (root / "scripts" / "cgroup_mem.py").write_text(
            'import json,sys; print(json.dumps({"containers":{},"worst":{"container":None,"pct":None,"anon":None,"limit":None}}))\n'
        )
    for d in ("data/host", "data/indiegen/runs", "data/cam", "music-audio", "music"):
        (root / d).mkdir(parents=True)
    (root / ".env").write_text("INDIEGEN_INTERVAL_SECONDS=21600\nHTTP_PORT=8090\n")
    (root / "music/catalog.json").write_text('{"tracks": []}')
    (root / "music-audio/track_s1.flac").write_bytes(b"x")
    # One cycle finished an hour ago.
    (root / "data/indiegen/state.json").write_text(
        json.dumps({"cycle": 1, "updated_at": time.time() - 3600})
    )
    # Git repo so rev-parse works
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "init"], cwd=root, check=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", STUB_DOCKER), ("curl", STUB_CURL)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    # Stub sudo to fail (no journal vacuum) and git already real
    return root


def _run(root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(root / "scripts" / "radio-job.sh"), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _status(root: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / "data/status.json").read_text())
    return data


def test_verify_writes_camera_reason(appliance: Path, tmp_path: Path) -> None:
    result = _run(appliance, tmp_path, "verify")
    assert result.returncode == 0, result.stderr + result.stdout

    data = _status(appliance)
    assert data["camera"]["reason"] == "container not running"
    assert data["camera"]["fresh"] is False


def test_verify_reports_library_age(appliance: Path, tmp_path: Path) -> None:
    result = _run(appliance, tmp_path, "verify")
    assert result.returncode == 0, result.stderr + result.stdout

    content = _status(appliance)["content"]
    assert 3500 <= content["last_render_age_s"] <= 3700
    assert content["next_render_in_s"] == 21600 - content["last_render_age_s"]


def test_overdue_render_reports_zero(appliance: Path, tmp_path: Path) -> None:
    (appliance / "data/indiegen/state.json").write_text(
        json.dumps({"cycle": 1, "updated_at": time.time() - 90000})
    )

    result = _run(appliance, tmp_path, "verify")
    assert result.returncode == 0, result.stderr + result.stdout

    assert _status(appliance)["content"]["next_render_in_s"] == 0


def test_status_is_published_under_the_door(appliance: Path, tmp_path: Path) -> None:
    result = _run(appliance, tmp_path, "verify")
    assert result.returncode == 0, result.stderr + result.stdout

    published = appliance / "data/host/status.json"
    assert published.is_file(), "player reads /host/status.json"
    assert published.read_text() == (appliance / "data/status.json").read_text()


def test_deferrals_in_the_last_hour_are_reported(appliance: Path, tmp_path: Path) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200))
    (appliance / "data/job.log").write_text(
        f"{old} render deferred: hot: 95000mC\n{now} tts deferred: memory pressure: some avg60=42\n"
    )

    result = _run(appliance, tmp_path, "verify")
    assert result.returncode == 0, result.stderr + result.stdout

    deferred = _status(appliance)["jobs"]["deferred"]
    assert len(deferred) == 1, "only the last hour counts"
    assert deferred[0]["job"] == "tts"
    assert "memory pressure" in deferred[0]["reason"]


def test_status_command_prints_reason_and_library(appliance: Path, tmp_path: Path) -> None:
    result = _run(appliance, tmp_path, "verify")
    assert result.returncode == 0, result.stderr + result.stdout

    out = _run(appliance, tmp_path, "status")

    assert out.returncode == 0
    assert "camera" in out.stdout
    assert "container not running" in out.stdout
    assert "device absent" not in out.stdout
    assert "last render" in out.stdout
