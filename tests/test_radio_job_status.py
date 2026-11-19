"""Contract gate for `radio-job verify` → data/status.json (#104).

The roadmap's acceptance criterion for the status surface is "every field is
populated from a real probe", and the one that was faking it was `camera`: the
file carried an age and the text reader guessed the reason ("device absent?").
This pins the fields the file must carry and the fact that it is published
under the door at data/host/status.json, which is what the player reads.

A stub `docker` and `curl` stand in for the box, so the gate runs anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "radio-job.sh"

STUB_DOCKER = """#!/usr/bin/env bash
case "$1" in
  inspect)
    case "${@: -1}" in
      radio-cam) echo false ;;
      radio-liquidsoap) echo false ;;
      *) echo "{}" ;;
    esac
    ;;
  ps) exit 0 ;;
  exec)
    case "$*" in
      *status-json.xsl*) printf '{"icestats":{"source":{"title":"Test Title"}}}' ;;
      *tx_bytes*) echo 1000 ;;
      *) : ;;
    esac
    ;;
esac
exit 0
"""

STUB_CURL = """#!/usr/bin/env bash
# -w '%{http_code}' is the only caller; answer like a closed door.
printf '404'
"""


@pytest.fixture()
def appliance(tmp_path: Path) -> Path:
    root = tmp_path / "radio"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "radio-job.sh")
    for d in ("data/host", "data/indiegen/runs", "data/cam", "music-audio", "music"):
        (root / d).mkdir(parents=True)
    (root / ".env").write_text("INDIEGEN_INTERVAL_SECONDS=21600\n")
    (root / "music/catalog.json").write_text('{"tracks": []}')
    (root / "music-audio/track_s1.flac").write_bytes(b"x")
    # One cycle finished an hour ago, as the generator records it.
    (root / "data/indiegen/state.json").write_text(
        json.dumps({"cycle": 1, "updated_at": time.time() - 3600})
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", STUB_DOCKER), ("curl", STUB_CURL)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return root


def _run(root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(root / "scripts" / "radio-job.sh"), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _status(root: Path) -> dict:
    return json.loads((root / "data/status.json").read_text())


def test_verify_writes_valid_json_with_a_camera_reason(appliance, tmp_path) -> None:
    result = _run(appliance, tmp_path, "verify")
    assert result.returncode == 0, result.stderr

    data = _status(appliance)
    assert data["camera"]["reason"] == "container not running", (
        "the reason must come from a probe, not a guess"
    )
    assert data["camera"]["fresh"] is False


def test_verify_reports_library_age_from_the_generator_state(appliance, tmp_path) -> None:
    _run(appliance, tmp_path, "verify")

    content = _status(appliance)["content"]
    assert 3500 <= content["last_render_age_s"] <= 3700
    assert content["next_render_in_s"] == 21600 - content["last_render_age_s"]


def test_overdue_render_reports_zero_not_a_negative_countdown(appliance, tmp_path) -> None:
    (appliance / "data/indiegen/state.json").write_text(
        json.dumps({"cycle": 1, "updated_at": time.time() - 90000})
    )

    _run(appliance, tmp_path, "verify")

    assert _status(appliance)["content"]["next_render_in_s"] == 0


def test_status_is_published_under_the_door(appliance, tmp_path) -> None:
    _run(appliance, tmp_path, "verify")

    published = appliance / "data/host/status.json"
    assert published.is_file(), "the player reads /host/status.json; the file must exist there"
    assert published.read_text() == (appliance / "data/status.json").read_text()


def test_deferrals_in_the_last_hour_are_reported(appliance, tmp_path) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200))
    (appliance / "data/job.log").write_text(
        f"{old} render deferred: hot: 95000mC\n{now} tts deferred: memory pressure: some avg60=42\n"
    )

    _run(appliance, tmp_path, "verify")

    deferred = _status(appliance)["jobs"]["deferred"]
    assert len(deferred) == 1, "only the last hour counts"
    assert deferred[0]["job"] == "tts"
    assert "memory pressure" in deferred[0]["reason"]


def test_status_command_prints_the_probed_reason(appliance, tmp_path) -> None:
    _run(appliance, tmp_path, "verify")

    out = _run(appliance, tmp_path, "status")

    assert out.returncode == 0
    assert "camera" in out.stdout
    assert "container not running" in out.stdout
    assert "device absent" not in out.stdout, (
        "the reader must not guess a reason the file now carries"
    )
    assert "last render" in out.stdout
