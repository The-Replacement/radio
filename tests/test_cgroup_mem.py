"""Per-container memory pressure, read from the cgroup.

These tests pin the two things that make the numbers trustworthy: a missing
file must report "not measured" instead of zero, and a container that restarted
must be distinguishable from one that merely idled. The rest is parsing -- but
a parser that silently drops a container is a blind spot in the one place that
exists to remove blind spots.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "cgroup_mem.py"


def _load_module() -> Any:
    """Load the standalone script as a module, the way the box runs it."""
    spec = importlib.util.spec_from_file_location("cgroup_mem", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cgroup_mem = _load_module()


@pytest.fixture
def cgroup_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A fake /proc and /sys/fs/cgroup pair with one running container."""
    proc = tmp_path / "proc"
    root = tmp_path / "cgroup"
    proc.mkdir()
    root.mkdir()
    return proc, root


def _write_running_container(
    proc: Path,
    root: Path,
    *,
    pid: int = 42,
    scope: str = "/system.slice/docker-abc.scope",
    peak: int | None = 522_124_000,
    oom_kills: int | None = 5,
    anon: int | None = 434_128_000,
) -> Path:
    """Create the /proc entry and cgroup directory for one running container."""
    pid_dir = proc / str(pid)
    pid_dir.mkdir(parents=True)
    (pid_dir / "cgroup").write_text(f"0::{scope}\n")
    cgroup_dir = root / scope.lstrip("/")
    cgroup_dir.mkdir(parents=True)
    if peak is not None:
        (cgroup_dir / "memory.peak").write_text(f"{peak}\n")
    if anon is not None:
        (cgroup_dir / "memory.stat").write_text(f"anon {anon}\nfile 101847040\n")
    if oom_kills is not None:
        (cgroup_dir / "memory.events").write_text(
            f"low 0\nhigh 3\nmax 0\noom 2\noom_kill {oom_kills}\n"
        )
    return cgroup_dir


def test_cgroup_v2_path_returns_path_from_zero_line() -> None:
    text = "12:memory:/docker/abc\n0::/system.slice/docker-abc.scope\n"
    assert cgroup_mem.cgroup_v2_path(text) == "/system.slice/docker-abc.scope"


def test_cgroup_v2_path_returns_empty_for_cgroup_v1_only() -> None:
    text = "12:memory:/docker/abc\n11:cpu:/docker/abc\n"
    assert cgroup_mem.cgroup_v2_path(text) == ""


@pytest.mark.parametrize("line", ["", "garbage", "0::\n"])
def test_cgroup_v2_path_ignores_lines_that_are_not_v2(line: str) -> None:
    assert cgroup_mem.cgroup_v2_path(line) == ""


def test_read_peak_returns_bytes(cgroup_tree: tuple[Path, Path]) -> None:
    _, root = cgroup_tree
    cgroup_dir = root / "system.slice" / "docker-abc.scope"
    cgroup_dir.mkdir(parents=True)
    (cgroup_dir / "memory.peak").write_text("105066496\n")
    assert cgroup_mem.read_peak(cgroup_dir) == 105_066_496


def test_read_peak_missing_file_returns_none(cgroup_tree: tuple[Path, Path]) -> None:
    _, root = cgroup_tree
    cgroup_dir = root / "absent"
    cgroup_dir.mkdir()
    assert cgroup_mem.read_peak(cgroup_dir) is None


def test_read_peak_unreadable_value_returns_none(cgroup_tree: tuple[Path, Path]) -> None:
    _, root = cgroup_tree
    cgroup_dir = root / "garbage"
    cgroup_dir.mkdir()
    (cgroup_dir / "memory.peak").write_text("max\n")
    assert cgroup_mem.read_peak(cgroup_dir) is None


def test_read_oom_kills_returns_counter(cgroup_tree: tuple[Path, Path]) -> None:
    _, root = cgroup_tree
    cgroup_dir = root / "events"
    cgroup_dir.mkdir()
    (cgroup_dir / "memory.events").write_text("low 0\nhigh 3\noom 2\noom_kill 7\n")
    assert cgroup_mem.read_oom_kills(cgroup_dir) == 7


def test_read_oom_kills_missing_file_returns_none(cgroup_tree: tuple[Path, Path]) -> None:
    _, root = cgroup_tree
    cgroup_dir = root / "none"
    cgroup_dir.mkdir()
    assert cgroup_mem.read_oom_kills(cgroup_dir) is None


def test_read_anon_returns_anonymous_memory(cgroup_tree: tuple[Path, Path]) -> None:
    _, root = cgroup_tree
    cgroup_dir = root / "stat"
    cgroup_dir.mkdir()
    (cgroup_dir / "memory.stat").write_text("anon 53252096\nfile 101847040\nkernel_stack 16384\n")
    assert cgroup_mem.read_anon(cgroup_dir) == 53_252_096


def test_read_anon_missing_file_returns_none(cgroup_tree: tuple[Path, Path]) -> None:
    _, root = cgroup_tree
    cgroup_dir = root / "none-stat"
    cgroup_dir.mkdir()
    assert cgroup_mem.read_anon(cgroup_dir) is None


def test_container_entry_reads_peak_anon_and_oom_kills(cgroup_tree: tuple[Path, Path]) -> None:
    proc, root = cgroup_tree
    _write_running_container(proc, root, peak=522_124_000, anon=434_128_000, oom_kills=5)
    entry = cgroup_mem.container_entry(
        "radio-liquidsoap", 536_870_912, 0, 42, proc_root=proc, cgroup_root=root
    )
    assert entry == {
        "limit": 536_870_912,
        "restarts": 0,
        "peak": 522_124_000,
        "anon": 434_128_000,
        "oom_kills": 5,
    }


def test_container_entry_with_dead_pid_reports_none(cgroup_tree: tuple[Path, Path]) -> None:
    proc, root = cgroup_tree
    entry = cgroup_mem.container_entry(
        "radio-mario", 1_610_612_736, 0, 0, proc_root=proc, cgroup_root=root
    )
    assert entry == {
        "limit": 1_610_612_736,
        "restarts": 0,
        "peak": None,
        "anon": None,
        "oom_kills": None,
    }


def test_container_entry_with_missing_proc_entry_reports_none(
    cgroup_tree: tuple[Path, Path],
) -> None:
    proc, root = cgroup_tree
    entry = cgroup_mem.container_entry(
        "radio-gone", 1024, 3, 999999, proc_root=proc, cgroup_root=root
    )
    assert entry == {"limit": 1024, "restarts": 3, "peak": None, "anon": None, "oom_kills": None}


def test_container_entry_without_memory_peak_reports_none_not_zero(
    cgroup_tree: tuple[Path, Path],
) -> None:
    proc, root = cgroup_tree
    _write_running_container(proc, root, peak=None, anon=2048, oom_kills=0)
    entry = cgroup_mem.container_entry(
        "radio-old-kernel", 512, 0, 42, proc_root=proc, cgroup_root=root
    )
    assert entry["peak"] is None
    assert entry["anon"] == 2048
    assert entry["oom_kills"] == 0


def test_parse_inspect_line_strips_leading_slash() -> None:
    assert cgroup_mem.parse_inspect_line("/radio-liquidsoap 536870912 1 4242") == (
        "radio-liquidsoap",
        536_870_912,
        1,
        4242,
    )


@pytest.mark.parametrize("line", ["", "/radio-x 1 2", "/radio-x a b c"])
def test_parse_inspect_line_rejects_malformed_input(line: str) -> None:
    with pytest.raises(ValueError):
        cgroup_mem.parse_inspect_line(line)


def test_collect_reads_every_container(cgroup_tree: tuple[Path, Path]) -> None:
    proc, root = cgroup_tree
    _write_running_container(proc, root, pid=42, scope="/a.scope", peak=100, oom_kills=0)
    containers = cgroup_mem.collect(
        ["/radio-one 512 0 42", "/radio-two 1024 1 0"], proc_root=proc, cgroup_root=root
    )
    assert set(containers) == {"radio-one", "radio-two"}
    assert containers["radio-one"]["peak"] == 100
    assert containers["radio-two"]["peak"] is None


def test_collect_skips_blank_lines(cgroup_tree: tuple[Path, Path]) -> None:
    proc, root = cgroup_tree
    containers = cgroup_mem.collect(["\n", "  \n"], proc_root=proc, cgroup_root=root)
    assert containers == {}


def test_summarize_picks_the_container_closest_to_its_limit() -> None:
    containers = {
        "radio-idle": {
            "limit": 1000,
            "restarts": 0,
            "peak": 900,
            "anon": 10,
            "oom_kills": 0,
        },
        "radio-pressed": {
            "limit": 512,
            "restarts": 0,
            "peak": 512,
            "anon": 400,
            "oom_kills": 0,
        },
    }
    worst = cgroup_mem.summarize(containers)
    assert worst == {
        "container": "radio-pressed",
        "pct": 78.1,
        "anon": 400,
        "limit": 512,
    }


def test_summarize_ignores_page_cache_saturation() -> None:
    # Measured on the probe: peak == limit in 60 s with 53 MiB of anon, because
    # FLAC page cache filled the rest. That is not pressure and must not gate.
    containers = {
        "radio-reader": {
            "limit": 512 * 1024 * 1024,
            "restarts": 0,
            "peak": 512 * 1024 * 1024,
            "anon": 53 * 1024 * 1024,
            "oom_kills": 0,
        },
    }
    worst = cgroup_mem.summarize(containers)
    assert worst["pct"] == 10.4


def test_summarize_ignores_unmeasured_containers() -> None:
    containers = {
        "radio-stopped": {
            "limit": 512,
            "restarts": 0,
            "peak": None,
            "anon": None,
            "oom_kills": None,
        },
    }
    worst = cgroup_mem.summarize(containers)
    assert worst == {"container": None, "pct": None, "anon": None, "limit": None}


def test_summarize_ignores_zero_limit() -> None:
    containers = {
        "radio-uncapped": {
            "limit": 0,
            "restarts": 0,
            "peak": 4096,
            "anon": 4096,
            "oom_kills": 0,
        },
    }
    worst = cgroup_mem.summarize(containers)
    assert worst["container"] is None


def test_summarize_reports_above_one_hundred_when_past_the_limit() -> None:
    containers = {
        "radio-liquidsoap": {
            "limit": 100,
            "restarts": 3,
            "peak": 150,
            "anon": 150,
            "oom_kills": 2,
        },
    }
    worst = cgroup_mem.summarize(containers)
    assert worst["pct"] == 150.0


def test_main_prints_empty_document_for_no_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cgroup_mem.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "containers": {},
        "worst": {"container": None, "pct": None, "anon": None, "limit": None},
    }


def test_main_fails_loudly_on_an_unparseable_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("/radio-x not-a-number 0 42\n"))
    assert cgroup_mem.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unparseable docker inspect line" in captured.err
