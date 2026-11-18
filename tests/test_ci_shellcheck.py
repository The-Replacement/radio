"""The CI shellcheck gate must reach every script, not only the subdirectories.

#126: the workflow ran ``shellcheck -x scripts/**/*.sh``. Actions runs bash with
globstar off, so ``**`` behaves as a single ``*`` and the pattern resolved to
``scripts/lib/*.sh`` and ``scripts/smoke.d/*.sh`` — every top-level script
(``radio-job.sh``, ``spacer-race.sh``, the mario and rotation handlers) was
never checked by CI. The gate looked green because it was not looking.

These tests pin the property, not the spelling: whatever the command becomes,
it must name the top-level scripts or delegate to a depth-agnostic find.
"""

from __future__ import annotations

import shlex
from pathlib import Path

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _shellcheck_command() -> str:
    """Return the shellcheck step's command line from the workflow.

    Anchored to the step name: "install -y shellcheck" is also a run line that
    contains the word, and matching it would test the installer, not the gate.
    """
    lines = CI.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("- name:") and "Run shellcheck" in line:
            for follow in lines[index + 1 :]:
                if follow.strip().startswith("run:"):
                    return follow.strip().split("run:", 1)[1].strip()
    raise AssertionError(f"no 'Run shellcheck' step found in {CI}")


def test_ci_shellcheck_command_reaches_top_level_scripts() -> None:
    command = _shellcheck_command()
    covers_top_level = "scripts/*.sh" in command or "find scripts" in command
    assert covers_top_level, (
        "the shellcheck step does not reach top-level scripts/*.sh; "
        f"under globstar-off a bare 'scripts/**/*.sh' skips them: {command!r}"
    )


def test_ci_shellcheck_command_is_not_the_globstar_only_form() -> None:
    """The exact command that shipped the bug must never come back."""
    args = shlex.split(_shellcheck_command())
    assert args != ["shellcheck", "-x", "scripts/**/*.sh"]


def test_top_level_scripts_exist_and_are_named_by_the_gate() -> None:
    """A depth-agnostic find is accepted; a glob must at least name the files."""
    command = _shellcheck_command()
    if "find scripts" in command:
        return
    top_level = sorted(p.name for p in SCRIPTS.glob("*.sh"))
    assert top_level, "no top-level scripts found; the gate has nothing to check"
    for name in top_level:
        assert f"scripts/{name}" in command or "scripts/*.sh" in command, (
            f"{name} is not covered by the shellcheck gate: {command!r}"
        )
