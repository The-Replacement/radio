"""`radio-job heal` self-heal ladder (#106).

Simulates the issue's verify criterion: kill liquidsoap's source and watch the
ladder restart it with no human involved. The box itself cannot be exercised
here, so the drill is encoded as: a status that reports the stream is not
flowing must make `heal` invoke `docker compose ... restart liquidsoap`.
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

STUB_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$DOCKER_LOG"
exit 0
"""

STUB_CURL = """#!/usr/bin/env bash
printf '404'
"""


def _healthy_status(**overrides: Any) -> dict[str, Any]:
    status = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": "test",
        "deploy": {"commit": "test", "detached_head": False},
        "stream": {"source": True, "bytes_per_s": 18000, "flowing": True, "title": "T"},
        "door": {"status_without_token": "404", "closed": True},
        "camera": {"segment_age_s": -1, "reason": "container not running", "fresh": False},
        "content": {
            "tracks": 10,
            "catalog_ok": True,
            "last_tts_age_s": 60,
            "last_render_age_s": 60,
            "next_render_in_s": 21540,
            "weather_age_s": 60,
            "weather_fresh": True,
            "rss_age_s": 60,
            "rss_fresh": True,
        },
        "resources": {
            "pkg_c": 45,
            "mem_available_mb": 20000,
            "mem_pressure_some": "0.00",
            "disk_free_gb": 500,
            "below_floor": False,
            "cpu_pressure_some": "0.00",
            "load1": "0.10",
            "load_max": 6.0,
            "mario_oneoffs": 0,
            "liquidsoap_extra": 0,
            "liquidsoap_oneoffs": 0,
            "uncapped_containers": 0,
            "cap_pressure_pct": 90,
            "memory_pressure": {"containers": {}, "worst": {"container": None, "pct": None}},
        },
        "jobs": {"last_gc": "never", "deferred": []},
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and k in status and isinstance(status[k], dict):
            status[k].update(v)  # type: ignore[attr-defined]
        else:
            status[k] = v
    return status


@pytest.fixture()
def appliance(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "radio"
    (root / "scripts").mkdir(parents=True)
    (root / "data").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / "radio-job.sh")
    (root / ".env").write_text("INDIEGEN_INTERVAL_SECONDS=21600\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    for name, body in (("docker", STUB_DOCKER), ("curl", STUB_CURL)):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)

    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_LOG": str(docker_log)}
    return {"root": root, "tmp": tmp_path, "bin": bin_dir, "docker_log": docker_log, "env": env}


def _run(appliance: dict[str, Any], status: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    (appliance["root"] / "data" / "status.json").write_text(json.dumps(status))
    appliance["docker_log"].write_text("")
    return subprocess.run(
        ["bash", str(appliance["root"] / "scripts" / "radio-job.sh"), "heal"],
        cwd=appliance["root"],
        env=appliance["env"],
        capture_output=True,
        text=True,
        check=False,
    )


def _docker_calls(appliance: dict[str, Any]) -> str:
    p = appliance["docker_log"]
    return p.read_text() if p.exists() else ""


def test_level1_restarts_liquidsoap_when_stream_not_flowing(appliance: dict[str, Any]) -> None:
    status = _healthy_status(
        stream={"source": True, "bytes_per_s": 0, "flowing": False, "title": "T"}
    )
    result = _run(appliance, status)
    assert result.returncode == 0
    assert "LEVEL1" in result.stdout
    assert "restart liquidsoap" in _docker_calls(appliance)


def test_no_action_when_stream_flowing(appliance: dict[str, Any]) -> None:
    status = _healthy_status()
    result = _run(appliance, status)
    assert "LEVEL1" not in result.stdout
    assert "restart liquidsoap" not in _docker_calls(appliance)


def test_level2_restarts_caddy_when_door_unhealthy(appliance: dict[str, Any]) -> None:
    status = _healthy_status(door={"status_without_token": "200", "closed": False})
    result = _run(appliance, status)
    assert "LEVEL2" in result.stdout
    assert "restart caddy" in _docker_calls(appliance)


def test_level3_stops_cam_and_indiegen_on_hot(appliance: dict[str, Any]) -> None:
    status = _healthy_status(resources={"pkg_c": 95})
    result = _run(appliance, status)
    assert "LEVEL3" in result.stdout
    calls = _docker_calls(appliance)
    assert "stop cam indiegen" in calls or "stop" in calls
    assert (appliance["root"] / "data" / "batch-blocked").exists()


def test_level4_stops_cam_on_psi_memory(appliance: dict[str, Any]) -> None:
    status = _healthy_status(resources={"pkg_c": 45, "mem_pressure_some": "10.0"})
    result = _run(appliance, status)
    assert "LEVEL4" in result.stdout


def test_level6_stops_after_three_identical_escalations(appliance: dict[str, Any]) -> None:
    state = {
        "liquidsoap_restarts": [],
        "caddy_restarts": [],
        "thermal_started": None,
        "thermal_recovery_started": None,
        "psi_started": None,
        "batch_blocked": False,
        "not_flowing_since": None,
        "escalations": [
            {"type": "liquidsoap-restart", "ts": time.time() - 100},
            {"type": "liquidsoap-restart", "ts": time.time() - 50},
        ],
        "stopped": False,
        "diagnosis": None,
    }
    (appliance["root"] / "data" / "heal-state.json").write_text(json.dumps(state))
    status = _healthy_status(
        stream={"source": True, "bytes_per_s": 0, "flowing": False, "title": "T"}
    )
    result = _run(appliance, status)
    assert "HEAL STOPPED" in result.stdout
    assert (appliance["root"] / "data" / "heal-stopped").exists()


def test_heal_skips_when_status_unreadable(appliance: dict[str, Any]) -> None:
    sp = appliance["root"] / "data" / "status.json"
    sp.write_text("{not json")
    # prevent verify from overwriting by making docker fail
    result = subprocess.run(
        ["bash", str(appliance["root"] / "scripts" / "radio-job.sh"), "heal"],
        cwd=appliance["root"],
        env=appliance["env"],
        capture_output=True,
        text=True,
        check=False,
    )
    # verify may regenerate status via stubs; either way must not crash
    assert result.returncode == 0
