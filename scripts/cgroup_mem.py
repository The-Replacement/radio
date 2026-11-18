#!/usr/bin/env python3
"""Per-container memory pressure: peak and OOM kills, straight from the cgroup.

WHY
---
On 2026-11-16 the new 512 MiB cap OOM-killed ``radio-liquidsoap`` twelve times
in one 3.5 h window. The kernel journal had the evidence, the status surface had
nothing: ``radio-job verify`` reported ``0 uncapped container(s)`` and called
that healthy. A cap that is never measured is a cap that kills silently.

``memory.stat`` (anon), ``memory.peak`` and ``memory.events`` live in the
container's own cgroup, so reading them answers "how close did this get to its
limit" without sampling ``docker stats`` and without a database. Pressure is
measured on **anonymous** memory: the kernel reclaims page cache first, and a
probe on 2026-11-18 saturated ``memory.peak`` at the cap in 60 s with 53 MiB of
anon because reading FLAC files charged the rest as cache. The five-minute
verify timer turns a real climb into a number; at 90 % the gate stops handing
the box more work while it is already under pressure.

WHAT IT CANNOT DO
-----------------
The counters belong to the cgroup, and the cgroup is recreated when the
container restarts. A peak therefore measures *since the last start*, and a
process killed by the OOM killer takes its own ``oom_kill`` count with it. That
is why ``restarts`` is recorded next to the numbers: a peak that resets while
the restart count climbs is a death, not a clean start. The kernel journal
remains the only lifetime record.

STANDALONE BY DESIGN
--------------------
Standard library only, no project imports: this runs on the box with nothing
but ``python3``. It reads no docker socket itself -- ``docker inspect`` output
is piped in, one container per line:

    docker inspect --format '{{.Name}} {{.HostConfig.Memory}} \
{{.RestartCount}} {{.State.Pid}}' <ids> | python3 scripts/cgroup_mem.py

Output (stdout): one JSON object, ``{"containers": {...}, "worst": {...}}``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TypedDict

CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_ROOT = Path("/proc")


class ContainerMemory(TypedDict):
    """One container's memory limit and the pressure measured against it."""

    limit: int
    restarts: int
    peak: int | None
    anon: int | None
    oom_kills: int | None


class WorstPressure(TypedDict):
    """The container closest to its limit, or an empty record if none is measured."""

    container: str | None
    pct: float | None
    anon: int | None
    limit: int | None


def cgroup_v2_path(cgroup_file: str) -> str:
    """Return the cgroup v2 path from ``/proc/<pid>/cgroup`` contents.

    The v2 line is ``0::/path``. Returns an empty string when there is no v2
    line, because a cgroup v1 host has no ``memory.peak`` to read.
    """
    for line in cgroup_file.splitlines():
        parts = line.strip().split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            return parts[2]
    return ""


def read_peak(cgroup_dir: Path) -> int | None:
    """Return ``memory.peak`` in bytes, or None when the kernel does not expose it."""
    try:
        return int((cgroup_dir / "memory.peak").read_text().strip())
    except (OSError, ValueError):
        return None


def read_oom_kills(cgroup_dir: Path) -> int | None:
    """Return the ``oom_kill`` counter from ``memory.events``, or None."""
    try:
        for line in (cgroup_dir / "memory.events").read_text().splitlines():
            key, _, value = line.strip().partition(" ")
            if key == "oom_kill":
                return int(value)
    except (OSError, ValueError):
        return None
    return None


def read_anon(cgroup_dir: Path) -> int | None:
    """Return the ``anon`` total from ``memory.stat``, or None.

    This is the number that decides a cgroup OOM: the kernel reclaims page cache
    first, so a container whose cache fills the limit is not in trouble. Measured
    on 2026-11-18: a probe container hit ``memory.peak`` == its 512 MiB limit in
    60 s with only 53 MiB of anon, because decoding FLAC files charged the rest
    as page cache. Pressure must be measured on anon or every reader looks dead.
    """
    try:
        for line in (cgroup_dir / "memory.stat").read_text().splitlines():
            key, _, value = line.strip().partition(" ")
            if key == "anon":
                return int(value)
    except (OSError, ValueError):
        return None
    return None


def parse_inspect_line(line: str) -> tuple[str, int, int, int]:
    """Parse one ``docker inspect`` line into (name, limit, restarts, pid).

    The template is ``{{.Name}} {{.HostConfig.Memory}} {{.RestartCount}}
    {{.State.Pid}}``; ``.Name`` carries a leading slash. Raises ValueError on
    anything else: a silently skipped container would be a blind spot.
    """
    name, limit, restarts, pid = line.split()
    return name.lstrip("/"), int(limit), int(restarts), int(pid)


def container_entry(
    name: str,
    limit: int,
    restarts: int,
    pid: int,
    proc_root: Path = PROC_ROOT,
    cgroup_root: Path = CGROUP_ROOT,
) -> ContainerMemory:
    """Read one container's peak and OOM kills from its cgroup.

    A stopped container has ``pid`` 0 and no readable cgroup: the counters are
    reported as None rather than as zero, because zero would claim a measurement
    that never happened.
    """
    peak: int | None = None
    anon: int | None = None
    oom_kills: int | None = None
    if pid > 0:
        try:
            cgroup_file = (proc_root / str(pid) / "cgroup").read_text()
        except OSError:
            cgroup_file = ""
        path = cgroup_v2_path(cgroup_file)
        if path:
            cgroup_dir = cgroup_root / path.lstrip("/")
            peak = read_peak(cgroup_dir)
            anon = read_anon(cgroup_dir)
            oom_kills = read_oom_kills(cgroup_dir)
    return {
        "limit": limit,
        "restarts": restarts,
        "peak": peak,
        "anon": anon,
        "oom_kills": oom_kills,
    }


def collect(
    lines: Iterable[str],
    proc_root: Path = PROC_ROOT,
    cgroup_root: Path = CGROUP_ROOT,
) -> dict[str, ContainerMemory]:
    """Build the container map from ``docker inspect`` lines."""
    containers: dict[str, ContainerMemory] = {}
    for line in lines:
        if not line.strip():
            continue
        name, limit, restarts, pid = parse_inspect_line(line)
        containers[name] = container_entry(
            name, limit, restarts, pid, proc_root=proc_root, cgroup_root=cgroup_root
        )
    return containers


def summarize(containers: Mapping[str, ContainerMemory]) -> WorstPressure:
    """Return the container whose anonymous memory uses the largest share of its limit.

    Anonymous memory is what a cgroup OOM acts on: page cache is reclaimed
    first. ``memory.peak`` is reported for context but must not drive a
    threshold -- decoding a few FLAC files charges enough cache to saturate it.
    Containers without a measurement (stopped, or cgroup v1) are ignored: an
    unmeasured container must not be reported as idle. The ratio is not capped,
    so a container that exceeded its limit reports above 100.
    """
    worst: WorstPressure = {"container": None, "pct": None, "anon": None, "limit": None}
    for name, entry in containers.items():
        anon = entry["anon"]
        limit = entry["limit"]
        if anon is None or limit <= 0:
            continue
        pct = round(100.0 * anon / limit, 1)
        if worst["pct"] is None or pct > worst["pct"]:
            worst = {"container": name, "pct": pct, "anon": anon, "limit": limit}
    return worst


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read per-container memory peak and OOM kills from the cgroup.",
        epilog=(
            "Reads 'docker inspect' lines on stdin and prints "
            '{"containers": {...}, "worst": {...}} to stdout.'
        ),
    )
    parser.add_argument(
        "--cgroup-root",
        default=str(CGROUP_ROOT),
        help=f"cgroup v2 mount point (default: {CGROUP_ROOT})",
    )
    parser.add_argument(
        "--proc-root",
        default=str(PROC_ROOT),
        help=f"proc mount point (default: {PROC_ROOT})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        containers = collect(
            sys.stdin, proc_root=Path(args.proc_root), cgroup_root=Path(args.cgroup_root)
        )
    except ValueError as exc:
        print(f"cgroup-mem: unparseable docker inspect line: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"containers": containers, "worst": summarize(containers)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
