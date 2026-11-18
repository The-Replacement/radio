"""The CI shellcheck gate must reach every script, not only the subdirectories.

#126: the workflow ran ``shellcheck -x scripts/**/*.sh``. Actions runs bash with
globstar off, so ``**`` behaves as a single ``*`` and the pattern resolved to
``scripts/lib/*.sh`` and ``scripts/smoke.d/*.sh`` — every top-level script
(``radio-job.sh``, ``spacer-race.sh``, the mario and rotation handlers) was
never checked by CI, and the gate looked green because it was not looking.

The same defect has a second form: selecting by suffix. ``scripts/post-receive``
is the deploy hook, a ``/bin/sh`` script with no extension, and a ``-name
'*.sh'`` scan never sees it either.

These tests pin the properties the gate must keep, not the spelling of the
command: it must discover shell scripts at any depth, by shebang or by a
depth-agnostic find; it must actually run ``shellcheck -x``; and a run that
discovers nothing must fail instead of passing over an empty file list.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml  # type: ignore[import-untyped]

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# The command that shipped the bug. Never acceptable, at any verbosity.
GLOBSTAR_ONLY = ["shellcheck", "-x", "scripts/**/*.sh"]


def _first_line(path: Path) -> str:
    with path.open("rb") as handle:
        return handle.readline().decode("utf-8", "replace").strip()


def _gate_command() -> str:
    """Return the shellcheck gate's command text from the workflow.

    Selected by step name: the installer step ("Install shellcheck") is also a
    ``run`` line that contains the word, and testing it would test apt, not the
    gate. Exactly one step must match, or the parsing is wrong and the test
    should say so rather than silently check the wrong thing.
    """
    workflow = yaml.safe_load(CI.read_text())
    steps = workflow["jobs"]["shellcheck"]["steps"]
    matches = [
        step["run"]
        for step in steps
        if "shellcheck" in (step.get("name") or "").lower()
        and "install" not in (step.get("name") or "").lower()
        and step.get("run")
    ]
    assert len(matches) == 1, (
        f"expected exactly one shellcheck gate step in {CI}, got {len(matches)}"
    )
    command: str = matches[0]
    return command


def test_gate_runs_shellcheck_recursively() -> None:
    command = _gate_command()
    assert "shellcheck -x" in command, (
        f"the gate does not run 'shellcheck -x' (follow sources): {command!r}"
    )


def test_gate_is_not_the_globstar_only_form() -> None:
    """The exact command that shipped #126 must never come back."""
    for line in _gate_command().splitlines():
        assert shlex.split(line) != GLOBSTAR_ONLY, (
            "the gate is back to 'shellcheck -x scripts/**/*.sh': Actions runs bash "
            "with globstar off, so it skips every top-level scripts/*.sh"
        )


def test_discovery_reaches_every_depth() -> None:
    """Top-level scripts are the ones that were missed: the glob must recurse."""
    command = _gate_command()
    assert "globstar" in command or "find scripts" in command, (
        f"discovery is not depth-agnostic (no globstar, no find): {command!r}"
    )


def test_discovery_is_not_suffix_only() -> None:
    """A ``*.sh`` scan misses scripts/post-receive, which is the deploy hook."""
    command = _gate_command()
    suffix_only = "*.sh" in command and "head -n 1" not in command
    assert not suffix_only or "post-receive" in command, (
        "discovery selects *.sh only: scripts/post-receive is a /bin/sh script with "
        f"no suffix and would never be checked: {command!r}"
    )


def test_empty_discovery_fails_the_gate() -> None:
    """Green over zero files is the bug in a different coat."""
    command = _gate_command()
    assert "-eq 0" in command and "exit 1" in command, (
        f"the gate passes when it discovers nothing instead of failing: {command!r}"
    )


def test_shell_scripts_exist_under_scripts() -> None:
    """Guard for the guard: if scripts/ were empty, the gate would mean nothing."""
    shell_scripts = [
        path
        for path in SCRIPTS.rglob("*")
        if path.is_file() and _first_line(path).startswith("#!") and "sh" in _first_line(path)
    ]
    assert shell_scripts, f"no shell scripts found under {SCRIPTS}"
