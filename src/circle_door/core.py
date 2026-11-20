"""Core operations for the circle-door token CLI."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .matcher import MatcherError, generate_matcher

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class Paths:
    root: Path
    tokens_file: Path
    caddy_file: Path
    compose_file: Path
    generator: Path
    env_file: Path


def _find_root(start: Path | None = None) -> Path:
    """Best-effort repo root detection (directory containing docker-compose.yml/scripts)."""
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "docker-compose.yml").exists() and (cand / "scripts").exists():
            return cand
    # fallback: cwd
    return (start or Path.cwd()).resolve()


def resolve_paths(root: Path | None = None) -> Paths:
    root = _find_root(root)
    tokens_file = Path(os.environ.get("DOOR_TOKEN_FILE") or (root / "secrets" / "tokens.txt"))
    caddy_file = Path(os.environ.get("DOOR_MATCHER_FILE") or (root / "caddy" / "tokens.caddy"))
    compose_file = Path(os.environ.get("DOOR_COMPOSE") or (root / "docker-compose.yml"))
    generator = root / "scripts" / "token-matcher.py"
    env_file = root / ".env"
    return Paths(
        root=root,
        tokens_file=tokens_file,
        caddy_file=caddy_file,
        compose_file=compose_file,
        generator=generator,
        env_file=env_file,
    )


def ensure_secret_permissions(paths: Paths) -> None:
    if "DOOR_TOKEN_FILE" not in os.environ:
        secrets_dir = paths.root / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(secrets_dir, 0o700)
        except OSError:
            pass
    paths.tokens_file.parent.mkdir(parents=True, exist_ok=True)
    paths.tokens_file.touch(exist_ok=True)
    try:
        os.chmod(paths.tokens_file, 0o600)
    except OSError:
        pass
    if paths.caddy_file.exists():
        try:
            os.chmod(paths.caddy_file, 0o600)
        except OSError:
            pass


def random_val() -> str:
    # 32 bytes -> 64 hex chars, matching token.sh's openssl rand -hex 32
    import secrets as _secrets

    return _secrets.token_hex(32)


def validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise ValueError(
            f"Invalid name: {name!r}. Use only letters, numbers, hyphen or underscore."
        )


def normalize_expiry(arg: str) -> str:
    arg = arg.strip()
    if not arg:
        return ""
    m = re.fullmatch(r"(\d+)d", arg)
    if m:
        return (date.today() + timedelta(days=int(m.group(1)))).isoformat()
    try:
        d = date.fromisoformat(arg)
    except ValueError:
        raise ValueError(f"invalid:{arg}") from None
    if d < date.today():
        raise ValueError(f"past:{arg}")
    return d.isoformat()


def read_env_var(paths: Paths, key: str) -> str:
    if not paths.env_file.exists():
        return ""
    try:
        for line in paths.env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1]
    except OSError:
        return ""
    return ""


def public_url(paths: Paths) -> str:
    public = read_env_var(paths, "PUBLIC_URL") or "http://localhost:8090"
    return public.rstrip("/")


def url_for(paths: Paths, token: str) -> str:
    return f"{public_url(paths)}/?t={token}"


def cam_url_for(paths: Paths, token: str) -> str:
    return f"{public_url(paths)}/cam/{token}/index.m3u8"


def validate_environment(paths: Paths, *, allow_insecure_cookie: bool | None = None) -> None:
    site = read_env_var(paths, "SITE_ADDRESS")
    public_value = read_env_var(paths, "PUBLIC_URL")
    flags = read_env_var(paths, "COOKIE_FLAGS")
    if allow_insecure_cookie is None:
        allow_insecure_cookie = os.environ.get("ALLOW_INSECURE_COOKIE", "0") == "1"
    if (
        site.startswith("https://") or public_value.startswith("https://")
    ) and "Secure" not in flags:
        print(
            "ERROR: HTTPS is configured (SITE_ADDRESS/PUBLIC_URL) but COOKIE_FLAGS has no 'Secure'.",
            file=sys.stderr,
        )
        print("       Add '; Secure' to COOKIE_FLAGS for production.", file=sys.stderr)
        print(
            "       For an intentional exception (not recommended), rerun with ALLOW_INSECURE_COOKIE=1.",
            file=sys.stderr,
        )
        if not allow_insecure_cookie:
            raise SystemExit(1)


def compose_base(paths: Paths) -> list[str]:
    return ["docker", "compose", "-f", str(paths.compose_file)]


def caddy_image(paths: Paths) -> str:
    env_img = os.environ.get("CADDY_IMAGE")
    if env_img:
        return env_img
    try:
        for line in paths.compose_file.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("image:") and "caddy:" in s:
                return s.split("image:", 1)[1].strip()
    except OSError:
        pass
    return "caddy:2-alpine"


def validate_caddy(paths: Paths) -> bool:
    compose = compose_base(paths)
    try:
        ps = subprocess.run(
            [*compose, "ps", "--status", "running", "--services"],
            capture_output=True,
            text=True,
            check=False,
        )
        running = {ln.strip() for ln in ps.stdout.splitlines() if ln.strip()}
    except Exception:  # noqa: BLE001
        running = set()

    if "caddy" in running:
        r = subprocess.run(
            [
                *compose,
                "exec",
                "-T",
                "caddy",
                "caddy",
                "validate",
                "--config",
                "/etc/caddy/Caddyfile",
                "--adapter",
                "caddyfile",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return r.returncode == 0

    env = os.environ.copy()
    env.setdefault("ICECAST_UPSTREAM", os.environ.get("ICECAST_UPSTREAM", "icecast:8000"))
    env.setdefault("MEDIAMTX_UPSTREAM", os.environ.get("MEDIAMTX_UPSTREAM", "mediamtx:8889"))
    env.setdefault(
        "FRONTEND_URL",
        os.environ.get(
            "FRONTEND_URL", read_env_var(paths, "FRONTEND_URL") or "http://localhost:8090"
        ),
    )
    env.setdefault(
        "COOKIE_FLAGS", os.environ.get("COOKIE_FLAGS", read_env_var(paths, "COOKIE_FLAGS") or "")
    )
    r = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "ICECAST_UPSTREAM",
            "-e",
            "MEDIAMTX_UPSTREAM",
            "-e",
            "FRONTEND_URL",
            "-e",
            "COOKIE_FLAGS",
            "-v",
            f"{paths.caddy_file.parent}:/etc/caddy:ro",
            caddy_image(paths),
            "caddy",
            "validate",
            "--config",
            "/etc/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        env=env,
    )
    return r.returncode == 0


def regenerate_matcher(paths: Paths) -> bool:
    new_file = Path(str(paths.caddy_file) + ".new_file")
    backup_fd, backup_name = tempfile.mkstemp(prefix="tokens.caddy.backup.")
    os.close(backup_fd)
    backup = Path(backup_name)
    try:
        try:
            # Prefer external generator script for compatibility if present; fall back to in-process.
            if paths.generator.exists():
                r = subprocess.run(
                    [sys.executable, str(paths.generator), str(paths.tokens_file), str(new_file)],
                    check=False,
                )
                if r.returncode != 0:
                    raise MatcherError("generator failed")
            else:
                generate_matcher(paths.tokens_file, new_file)
        except Exception:  # noqa: BLE001
            print(
                "ERROR: could not generate the matcher; the current config was not touched.",
                file=sys.stderr,
            )
            try:
                new_file.unlink(missing_ok=True)
            except OSError:
                pass
            return False

        if paths.caddy_file.exists():
            # copy contents to preserve backup as separate inode/file (like mktemp target)
            backup.write_bytes(paths.caddy_file.read_bytes())
        else:
            # ensure empty backup
            backup.write_bytes(b"")

        # In-place write: keep inode container has mounted.
        paths.caddy_file.write_bytes(new_file.read_bytes())
        try:
            os.chmod(paths.caddy_file, 0o600)
        except OSError:
            pass
        try:
            new_file.unlink(missing_ok=True)
        except OSError:
            pass

        if not validate_caddy(paths):
            print(
                "ERROR: Caddy does not accept the generated config. Restoring the previous one.",
                file=sys.stderr,
            )
            if backup.exists():
                paths.caddy_file.write_bytes(backup.read_bytes())
            print("See the detail with:", file=sys.stderr)
            env_ice = os.environ.get("ICECAST_UPSTREAM", "icecast:8000")
            env_med = os.environ.get("MEDIAMTX_UPSTREAM", "mediamtx:8889")
            env_fe = os.environ.get("FRONTEND_URL", "http://localhost:8090")
            env_cf = os.environ.get("COOKIE_FLAGS", "")
            print(
                f'  docker run --rm -e ICECAST_UPSTREAM="{env_ice}" -e MEDIAMTX_UPSTREAM="{env_med}" -e FRONTEND_URL="{env_fe}" -e COOKIE_FLAGS="{env_cf}" -v "{paths.caddy_file.parent}:/etc/caddy:ro" "{caddy_image(paths)}" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile',
                file=sys.stderr,
            )
            return False
        return True
    finally:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass


def reload_caddy(paths: Paths) -> None:
    compose = compose_base(paths)
    try:
        ps = subprocess.run(
            [*compose, "ps", "--status", "running", "--services"],
            capture_output=True,
            text=True,
            check=False,
        )
        running = {ln.strip() for ln in ps.stdout.splitlines() if ln.strip()}
    except Exception:  # noqa: BLE001
        running = set()

    if "caddy" in running:
        print("-> reloading Caddy (without cutting the listeners)...")
        r = subprocess.run(
            [
                *compose,
                "exec",
                "-T",
                "caddy",
                "caddy",
                "reload",
                "--config",
                "/etc/caddy/Caddyfile",
                "--adapter",
                "caddyfile",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if r.returncode == 0:
            print("-> done.")
        else:
            print("-> could not reload hot; restarting the container.", file=sys.stderr)
            subprocess.run([*compose, "restart", "caddy"], stdout=subprocess.DEVNULL, check=False)
            print("-> done.")
    else:
        print("-> Caddy is not running; the change applies when it starts.")


def read_rows(tokens_file: Path) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    if not tokens_file.exists():
        return rows
    for line in tokens_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name, token = parts[0], parts[1]
        cookie = parts[2] if len(parts) >= 3 else ""
        exp = parts[3] if len(parts) >= 4 else ""
        rows.append((name, token, cookie, exp))
    return rows


def find_row(paths: Paths, name: str) -> tuple[str, str, str, str] | None:
    for row in read_rows(paths.tokens_file):
        if row[0] == name:
            return row
    return None


def write_rows_exact(paths: Paths, lines: list[str]) -> None:
    # preserve exact line endings/content as provided
    data = "".join(lines)
    paths.tokens_file.write_text(data, encoding="utf-8")
    try:
        os.chmod(paths.tokens_file, 0o600)
    except OSError:
        pass


def cmd_add(paths: Paths, name: str, requested_expiry: str = "") -> int:
    validate_name(name)
    validate_environment(paths)

    try:
        expiry = normalize_expiry(requested_expiry)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("past:"):
            print(f"That date has already passed: {msg.split(':', 1)[1]!r}", file=sys.stderr)
        else:
            val = msg.split(":", 1)[1] if ":" in msg else requested_expiry
            print(f"Invalid expiry: {val!r}. Use 30d or 2026-12-31", file=sys.stderr)
        return 1

    if find_row(paths, name):
        row = find_row(paths, name)
        v = row[3] if row else ""
        print(f'"{name}" already has access.', file=sys.stderr)
        if v:
            print(f"(expires {v})", file=sys.stderr)
        print("Credential links are sensitive and are not printed by default.", file=sys.stderr)
        print(f'Run: ./scripts/token.sh reveal "{name}"', file=sys.stderr)
        return 0

    t = random_val()
    c = random_val()
    with paths.tokens_file.open("a", encoding="utf-8") as fh:
        if expiry:
            fh.write(f"{name} {t} {c} {expiry}\n")
        else:
            fh.write(f"{name} {t} {c}\n")
    try:
        os.chmod(paths.tokens_file, 0o600)
    except OSError:
        pass

    if not regenerate_matcher(paths):
        return 1
    print(f'OK access granted to "{name}"')
    if expiry:
        print(f"   expires {expiry} (that day included)")
    else:
        print(
            f'   no expiry set (long-lived). Prefer an expiry, e.g. ./scripts/token.sh add "{name}" 30d',
            file=sys.stderr,
        )
    print()
    print("  Credential links are sensitive (bearer tokens) and are not printed by default.")
    print("  To display the invitation and camera URLs now, run:")
    print(f'    ./scripts/token.sh reveal "{name}"')
    print()
    print(
        "  Treat revealed links as secrets: avoid logs, shell history, screenshots, and unencrypted chats."
    )
    print(f'  Revoke when no longer needed: ./scripts/token.sh rm "{name}"')
    print()
    reload_caddy(paths)
    return 0


def cmd_reveal(paths: Paths, name: str) -> int:
    validate_name(name)
    row = find_row(paths, name)
    if not row:
        print(f'"{name}" was not on the list.', file=sys.stderr)
        return 1
    _, t, _c, v = row
    print(f'Credential for "{name}" (sensitive - do not share publicly/log):')
    if v:
        print(f"  expires {v}")
    print()
    print(f"  Invitation (browser): {url_for(paths, t)}")
    print(f"  Camera (VLC/mpv):    {cam_url_for(paths, t)}")
    return 0


def cmd_rm(paths: Paths, name: str) -> int:
    validate_name(name)
    if not find_row(paths, name):
        print(f'"{name}" was not on the list.', file=sys.stderr)
        return 1
    # Mirror token.sh: grep -vE "^${name} " -> tmp || true; mv tmp tokens
    lines: list[str] = []
    if paths.tokens_file.exists():
        for line in paths.tokens_file.read_text(encoding="utf-8").splitlines(keepends=True):
            if re.match(rf"^{re.escape(name)} ", line):
                continue
            lines.append(line)
    tmp = paths.tokens_file.with_name(paths.tokens_file.name + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, paths.tokens_file)
    try:
        os.chmod(paths.tokens_file, 0o600)
    except OSError:
        pass
    if not regenerate_matcher(paths):
        return 1
    print(f'OK access revoked for "{name}" (token and cookie)')
    reload_caddy(paths)
    return 0


def cmd_ls(paths: Paths) -> int:
    if not paths.tokens_file.exists() or paths.tokens_file.stat().st_size == 0:
        print("(nobody has access yet)")
        return 0
    today = date.today().isoformat()
    print(f"{'WHO':<20} {'TOKEN':<16} EXPIRES")
    for n, t, _c, v in read_rows(paths.tokens_file):
        masked = f"****{t[-4:]}" if len(t) >= 4 else "****"
        if not v:
            print(f"{n:<20} {masked:<16} never")
        elif v < today:
            print(f"{n:<20} {masked:<16} {v}  <-- EXPIRED")
        else:
            print(f"{n:<20} {masked:<16} {v}")
    return 0


def cmd_regen(paths: Paths) -> int:
    validate_environment(paths)
    if not regenerate_matcher(paths):
        return 1
    print("OK caddy/tokens.caddy regenerated from secrets/tokens.txt")
    reload_caddy(paths)
    return 0


def cmd_rotate(paths: Paths, name: str, requested_expiry: str = "") -> int:
    validate_name(name)
    validate_environment(paths)
    row = find_row(paths, name)
    if not row:
        print(f'"{name}" was not on the list.', file=sys.stderr)
        return 1
    current_expiry = row[3]
    if requested_expiry:
        try:
            new_expiry = normalize_expiry(requested_expiry)
        except ValueError as e:
            msg = str(e)
            if msg.startswith("past:"):
                print(f"That date has already passed: {msg.split(':', 1)[1]!r}", file=sys.stderr)
            else:
                val = msg.split(":", 1)[1] if ":" in msg else requested_expiry
                print(f"Invalid expiry: {val!r}. Use 30d or 2026-12-31", file=sys.stderr)
            return 1
    else:
        new_expiry = current_expiry

    t = random_val()
    c = random_val()
    lines: list[str] = []
    for line in paths.tokens_file.read_text(encoding="utf-8").splitlines(keepends=True):
        if re.match(rf"^{re.escape(name)} ", line):
            if new_expiry:
                lines.append(f"{name} {t} {c} {new_expiry}\n")
            else:
                lines.append(f"{name} {t} {c}\n")
        else:
            lines.append(line)
    tmp = paths.tokens_file.with_name(paths.tokens_file.name + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, paths.tokens_file)
    try:
        os.chmod(paths.tokens_file, 0o600)
    except OSError:
        pass
    if not regenerate_matcher(paths):
        return 1
    print(f'OK credentials rotated for "{name}" (old token/cookie revoked)')
    if new_expiry:
        print(f"   expires {new_expiry}")
    else:
        print(
            f'   no expiry set (long-lived). Prefer an expiry, e.g. ./scripts/token.sh rotate "{name}" 30d',
            file=sys.stderr,
        )
    print(f'   Reveal new links (sensitive): ./scripts/token.sh reveal "{name}"')
    reload_caddy(paths)
    return 0


def cmd_prune(paths: Paths) -> int:
    if not paths.tokens_file.exists() or paths.tokens_file.stat().st_size == 0:
        print("(no credentials file or empty)")
        return 0
    today = date.today().isoformat()
    kept: list[str] = []
    removed = 0
    for line in paths.tokens_file.read_text(encoding="utf-8").splitlines(keepends=True):
        parts = line.strip().split()
        if len(parts) >= 4 and parts[3] < today:
            removed += 1
            continue
        kept.append(line)
    if removed == 0:
        print("No expired credentials to prune.")
        return 0
    tmp = paths.tokens_file.with_name(paths.tokens_file.name + ".tmp")
    tmp.write_text("".join(kept), encoding="utf-8")
    os.replace(tmp, paths.tokens_file)
    try:
        os.chmod(paths.tokens_file, 0o600)
    except OSError:
        pass
    if not regenerate_matcher(paths):
        return 1
    print(f"OK pruned {removed} expired credential(s) from secrets/tokens.txt")
    reload_caddy(paths)
    return 0


def _stat_mode(p: Path) -> str:
    try:
        return oct(p.stat().st_mode & 0o777)[2:]
    except FileNotFoundError:
        return "missing"


def cmd_check(paths: Paths) -> int:
    fatal = 0
    warn = 0
    print("Security/lifecycle check (#87):")

    try:
        validate_environment(paths)
        print("  PASS cookie/transport config basics")
    except SystemExit:
        print("  FAIL cookie/transport config basics")
        fatal = 1

    secrets_dir = (
        paths.root / "secrets" if "DOOR_TOKEN_FILE" not in os.environ else paths.tokens_file.parent
    )
    dir_mode = _stat_mode(secrets_dir)
    tok_mode = _stat_mode(paths.tokens_file)
    caddy_mode = _stat_mode(paths.caddy_file)

    if dir_mode == "700":
        print("  PASS secrets/ permissions (700)")
    else:
        print(
            f"  WARN secrets/ permissions are {dir_mode} (expected 700); run token.sh to enforce or: chmod 700 secrets"
        )
        warn = 1
    if tok_mode == "600":
        print("  PASS secrets/tokens.txt permissions (600)")
    elif tok_mode == "missing":
        print("  WARN secrets/tokens.txt missing (no credentials stored)")
        warn = 1
    else:
        print(
            f"  WARN secrets/tokens.txt permissions are {tok_mode} (expected 600); run: chmod 600 secrets/tokens.txt"
        )
        warn = 1
    if caddy_mode == "600":
        print("  PASS caddy/tokens.caddy permissions (600)")
    elif caddy_mode == "missing":
        print("  WARN caddy/tokens.caddy missing; run: ./scripts/token.sh regen")
        warn = 1
    else:
        print(
            f"  WARN caddy/tokens.caddy permissions are {caddy_mode} (expected 600); run: chmod 600 caddy/tokens.caddy"
        )
        warn = 1

    today = date.today().isoformat()
    expired_in_storage = 0
    non_expiring = 0
    if paths.tokens_file.exists() and paths.tokens_file.stat().st_size > 0:
        for _n, _t, _c, v in read_rows(paths.tokens_file):
            if v and v < today:
                expired_in_storage += 1
            if not v:
                non_expiring += 1
        # Count rows as stored credentials including those without cookie in read_rows parsing? read_rows skips malformed
        # but for "non-expiring active credentials" token.sh uses: NF>=2 && (NF < 4 || $4 == "")
        # read_rows always returns 4-tuple; exp empty means non-expiring
    if expired_in_storage > 0:
        print(
            f"  WARN {expired_in_storage} expired credential(s) still in secrets/tokens.txt (omitted from matcher, but stored); run: ./scripts/token.sh prune"
        )
        warn = 1
    else:
        print("  PASS no expired credentials stored in secrets/tokens.txt")

    if non_expiring > 0:
        print(
            f"  WARN {non_expiring} active credential(s) have no expiry (long-lived); prefer expiry and rotation (e.g. ./scripts/token.sh rotate <name> 30d)"
        )
        warn = 1
    else:
        print("  PASS all stored credentials have expiry dates")

    print(
        "  INFO secrets/tokens.txt and caddy/tokens.caddy are bearer-equivalent; restrict host access/backups and avoid shell history, logs, screenshots, and unencrypted sharing. Use 'reveal' only when needed."
    )

    if fatal:
        print("FAIL security/lifecycle check found fatal issues.", file=sys.stderr)
        return 1
    if warn:
        print(
            "WARN security/lifecycle check passed fatal checks but found risks requiring attention.",
            file=sys.stderr,
        )
        return 0
    print("OK security/lifecycle checks passed.")
    return 0
