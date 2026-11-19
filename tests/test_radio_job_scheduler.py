"""`radio-job` runs one heavy job at a time (#105).

The gate is a snapshot taken before a job starts: two jobs that ask while the
box is idle both get a yes. The lock is what makes the second one wait, and
contention must read as a deferral (retried next tick), never as a failure.

The gate itself is exercised with a crafted fresh status.json, so these tests
need neither docker nor a real box.
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

# Stands in for mario-refresh.sh: records that it started, then holds the lock
# long enough for a second invocation to find it taken.
STUB_JOB = """#!/usr/bin/env bash
echo started > "$STUB_MARKER"
sleep "${STUB_SLEEP:-2}"
echo finished >> "$STUB_MARKER"
"""


@pytest.fixture()
def appliance(tmp_path: Path) -> dict:
    root = tmp_path / "radio"
    (root / "scripts").mkdir(parents=True)
    (root / "data").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / "radio-job.sh")
    (root / "scripts" / "mario-refresh.sh").write_text(STUB_JOB)
    (root / "scripts" / "mario-refresh.sh").chmod(0o755)

    # A fresh, healthy status: preflight reads it instead of running verify.
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
    (root / "data/status.json").write_text(json.dumps(status))
    return {"root": root, "tmp": tmp_path, "lock": tmp_path / "heavy.lock"}


def _env(appliance: dict, **extra: str) -> dict:
    marker = appliance["tmp"] / "marker"
    return {
        **os.environ,
        "STUB_MARKER": str(marker),
        "RADIO_JOB_HEAVY_LOCK": str(appliance["lock"]),
        **extra,
    }


def _run(appliance: dict, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(appliance["root"] / "scripts" / "radio-job.sh"), "tts"],
        cwd=appliance["root"],
        env=_env(appliance, **extra),
        capture_output=True,
        text=True,
        check=False,
    )


def _job_log(appliance: dict) -> str:
    return (appliance["root"] / "data/job.log").read_text()


def test_a_job_runs_when_the_lock_is_free(appliance) -> None:
    result = _run(appliance, STUB_SLEEP="0")

    assert result.returncode == 0
    assert (appliance["tmp"] / "marker").read_text().startswith("started")
    log = _job_log(appliance)
    assert "tts starting" in log
    assert "tts finished rc=0" in log


def test_a_second_heavy_job_defers_instead_of_stacking(appliance) -> None:
    marker = appliance["tmp"] / "marker"
    first = subprocess.Popen(
        ["bash", str(appliance["root"] / "scripts" / "radio-job.sh"), "tts"],
        cwd=appliance["root"],
        env=_env(appliance, STUB_SLEEP="3"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not marker.exists():
            time.sleep(0.05)
        assert marker.exists(), "the first job never started"

        second = _run(appliance, STUB_SLEEP="0")

        assert second.returncode == 0, "contention is a deferral, not a failure"
        assert "deferred: another heavy job holds" in second.stdout
        assert "started" in marker.read_text() and "finished" not in marker.read_text()
    finally:
        first.communicate(timeout=30)

    log = _job_log(appliance)
    assert "deferred: another heavy job holds" in log
    assert marker.read_text().count("started") == 1, "the second job must not have run the stub"


def test_the_lock_is_released_after_a_run(appliance) -> None:
    _run(appliance, STUB_SLEEP="0")
    (appliance["tmp"] / "marker").unlink()

    result = _run(appliance, STUB_SLEEP="0")

    assert "deferred" not in result.stdout
    assert (appliance["tmp"] / "marker").exists()


def test_a_closed_gate_reports_the_gate_reason_not_the_lock(appliance) -> None:
    status_path = appliance["root"] / "data/status.json"
    status = json.loads(status_path.read_text())
    status["resources"]["pkg_c"] = 95
    status_path.write_text(json.dumps(status))

    result = _run(appliance, STUB_SLEEP="0")

    assert "deferred: hot: 95C" in result.stdout
    assert not (appliance["tmp"] / "marker").exists()


def test_shadow_mode_takes_no_lock(appliance) -> None:
    result = _run(appliance, RADIO_JOB_SHADOW="1", STUB_SLEEP="0")

    assert "would run (shadow mode)" in result.stdout
    assert not (appliance["tmp"] / "marker").exists()
