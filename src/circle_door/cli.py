"""CLI for circle-door token management."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .core import (
    Paths,
    cmd_add,
    cmd_check,
    cmd_ls,
    cmd_prune,
    cmd_regen,
    cmd_reveal,
    cmd_rm,
    cmd_rotate,
    ensure_secret_permissions,
    resolve_paths,
)


def _paths(root: Path | None = None) -> Paths:
    paths = resolve_paths(root)
    ensure_secret_permissions(paths)
    return paths


def _run(argv: Sequence[str]) -> int:
    if not argv:
        _print_help()
        return 1
    cmd = argv[0]
    args = list(argv[1:])

    if cmd in ("--help", "-h", "help"):
        _print_help()
        return 1
    if cmd in ("--version", "version"):
        print(f"circle-door {__version__}")
        return 0

    if cmd == "add":
        if len(args) < 1:
            print("usage: circle-door add <name> [expiry]", file=sys.stderr)
            return 1
        return cmd_add(_paths(), args[0], args[1] if len(args) > 1 else "")
    if cmd in ("list", "ls"):
        return cmd_ls(_paths())
    if cmd in ("reveal", "show"):
        if len(args) < 1:
            print("usage: circle-door reveal <name>", file=sys.stderr)
            return 1
        return cmd_reveal(_paths(), args[0])
    if cmd in ("rm", "remove", "del"):
        if len(args) < 1:
            print("usage: circle-door rm <name>", file=sys.stderr)
            return 1
        return cmd_rm(_paths(), args[0])
    if cmd in ("regen", "sync"):
        return cmd_regen(_paths())
    if cmd == "rotate":
        if len(args) < 1:
            print("usage: circle-door rotate <name> [expiry]", file=sys.stderr)
            return 1
        return cmd_rotate(_paths(), args[0], args[1] if len(args) > 1 else "")
    if cmd == "prune":
        return cmd_prune(_paths())
    if cmd in ("check", "doctor"):
        return cmd_check(_paths())

    print(f"Unknown command: {cmd}", file=sys.stderr)
    _print_help()
    return 1


def _print_help() -> None:
    print(
        """usage: circle-door <command> [args]

Manage circle-door credentials (token store + Caddy matcher).

Commands:
  add <name> [expiry]      Grant access (creates credential; does not print links)
  reveal <name>            Print invitation/camera links (sensitive)
  rm <name>                Revoke access (token and cookie)
  list                     List who has access
  regen                    Regenerate Caddy matcher from the token store
  rotate <name> [expiry]   Reissue token/cookie (revokes old credential values)
  prune                    Remove expired credentials from the token store
  check                    Validate config and credential posture

Aliases: ls=list, show=reveal, remove/del=rm, sync=regen, doctor=check
"""
    )


# Optional Typer app for installed-package convention; stdlib CLI remains the fallback.
from typing import Any

app: Any = None
try:  # pragma: no cover - convenience wrapper
    import typer

    app = typer.Typer(
        add_completion=False,
        no_args_is_help=True,
        help="Manage circle-door credentials (token store + Caddy matcher).",
    )

    @app.callback(invoke_without_command=True)  # type: ignore[misc, untyped-decorator, unused-ignore]
    def _main_callback(
        ctx: typer.Context,
        version: bool = typer.Option(False, "--version", help="Show version and exit."),
    ) -> None:
        if version:
            print(f"circle-door {__version__}")
            raise typer.Exit(0)
        if ctx.invoked_subcommand is None:
            typer.echo(ctx.get_help())
            raise typer.Exit(1)

    @app.command()  # type: ignore[misc, untyped-decorator, unused-ignore]
    def add(name: str, expiry: str | None = typer.Argument(None)) -> None:
        raise typer.Exit(cmd_add(_paths(), name, expiry or ""))

    @app.command("list")  # type: ignore[misc, untyped-decorator, unused-ignore]
    def list_cmd() -> None:
        raise typer.Exit(cmd_ls(_paths()))

    @app.command("ls", hidden=True)  # type: ignore[misc, untyped-decorator, unused-ignore]
    def ls_cmd() -> None:
        raise typer.Exit(cmd_ls(_paths()))

    @app.command()  # type: ignore[misc, untyped-decorator, unused-ignore]
    def reveal(name: str) -> None:
        raise typer.Exit(cmd_reveal(_paths(), name))

    @app.command("show", hidden=True)  # type: ignore[misc, untyped-decorator, unused-ignore]
    def show_cmd(name: str) -> None:
        raise typer.Exit(cmd_reveal(_paths(), name))

    @app.command()  # type: ignore[misc, untyped-decorator, unused-ignore]
    def rm(name: str) -> None:
        raise typer.Exit(cmd_rm(_paths(), name))

    @app.command("remove", hidden=True)  # type: ignore[misc, untyped-decorator, unused-ignore]
    def remove_cmd(name: str) -> None:
        raise typer.Exit(cmd_rm(_paths(), name))

    @app.command("del", hidden=True)  # type: ignore[misc, untyped-decorator, unused-ignore]
    def del_cmd(name: str) -> None:
        raise typer.Exit(cmd_rm(_paths(), name))

    @app.command()  # type: ignore[misc, untyped-decorator, unused-ignore]
    def regen() -> None:
        raise typer.Exit(cmd_regen(_paths()))

    @app.command("sync", hidden=True)  # type: ignore[misc, untyped-decorator, unused-ignore]
    def sync_cmd() -> None:
        raise typer.Exit(cmd_regen(_paths()))

    @app.command()  # type: ignore[misc, untyped-decorator, unused-ignore]
    def rotate(name: str, expiry: str | None = typer.Argument(None)) -> None:
        raise typer.Exit(cmd_rotate(_paths(), name, expiry or ""))

    @app.command()  # type: ignore[misc, untyped-decorator, unused-ignore]
    def prune() -> None:
        raise typer.Exit(cmd_prune(_paths()))

    @app.command()  # type: ignore[misc, untyped-decorator, unused-ignore]
    def check() -> None:
        raise typer.Exit(cmd_check(_paths()))

    @app.command("doctor", hidden=True)  # type: ignore[misc, untyped-decorator, unused-ignore]
    def doctor_cmd() -> None:
        raise typer.Exit(cmd_check(_paths()))

except Exception:  # pragma: no cover  # noqa: BLE001
    app = None


def entrypoint() -> None:
    # Prefer stdlib dispatcher for zero-extra-dep execution in wrapper contexts.
    # Typer app remains available for package integrations (`circle_door.cli:app`).
    raise SystemExit(_run(sys.argv[1:]))


if __name__ == "__main__":
    entrypoint()
