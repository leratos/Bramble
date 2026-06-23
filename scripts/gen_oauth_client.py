"""Generate a confidential static OAuth client (the DCR fallback).

Phase-6 helper. Claude Web/Mobile normally register themselves via Dynamic
Client Registration (RFC 7591), so this is only needed when DCR is
unavailable on the client side and a pre-shared confidential client is
wanted instead.

It does **not** touch ``oauth.db`` directly. It emits the three env vars
that declare the static client; place them in the secrets env file the
service loads (``/opt/bramble/secrets/oauth.env``) and restart the service.
On startup :mod:`bramble.__main__` seeds/updates the client in ``oauth.db``
from those vars (idempotent), so the client survives a recreated database.

Usage
-----

    python scripts/gen_oauth_client.py \
        --redirect-uri https://claude.ai/api/mcp/auth_callback \
        --write /opt/bramble/secrets/oauth.env

The client_secret is generated fresh and written **only** to the mode-600 env
file; it is never printed to stdout (stdout lands in shell history / CI logs),
so ``--write`` is required. Read the secret back with ``cat`` to paste it into
the Claude connector. The public base URL is resolved from
``--public-base-url`` > ``BRAMBLE_OAUTH_PUBLIC_BASE_URL`` and is used only to
validate the configuration (https-for-non-local etc.).

Exit codes
----------
* ``0`` – credentials generated.
* ``2`` – bad argument / invalid configuration.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

# Make ``src/`` importable when running directly (mirrors scripts/gen_token.py).
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bramble.oauth_config import (  # noqa: E402  (sys.path setup above)
    ENV_OAUTH_PUBLIC_BASE_URL,
    ENV_OAUTH_STATIC_CLIENT_ID,
    ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS,
    ENV_OAUTH_STATIC_CLIENT_SECRET,
    OAuthConfig,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--redirect-uri",
        dest="redirect_uris",
        action="append",
        required=True,
        metavar="URL",
        help="allowed redirect URI (repeat for several).",
    )
    parser.add_argument(
        "--client-id",
        default=None,
        help="client id to use (default: a generated 'static-<hex>').",
    )
    parser.add_argument(
        "--public-base-url",
        default=None,
        help=(
            "public base URL, used only to validate the config "
            f"(env: {ENV_OAUTH_PUBLIC_BASE_URL})."
        ),
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        metavar="PATH",
        help="write the env block to PATH (mode 600) instead of printing it.",
    )
    return parser.parse_args(argv)


def resolve_public_base_url(
    cli_value: str | None, env: Mapping[str, str] | None = None
) -> str | None:
    if cli_value:
        return cli_value
    environ: Mapping[str, str] = os.environ if env is None else env
    return environ.get(ENV_OAUTH_PUBLIC_BASE_URL)


def render_env_block(
    client_id: str, client_secret: str, redirect_uris: tuple[str, ...]
) -> str:
    return "\n".join(
        (
            f"{ENV_OAUTH_STATIC_CLIENT_ID}={client_id}",
            f"{ENV_OAUTH_STATIC_CLIENT_SECRET}={client_secret}",
            f"{ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS}={' '.join(redirect_uris)}",
        )
    )


def write_env_file(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` with owner-only (600) permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    owner_only = stat.S_IRUSR | stat.S_IWUSR
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, owner_only)
    # The mode argument to os.open only applies when the file is CREATED. If the
    # target already existed (group/world-readable), tighten it explicitly so a
    # fresh secret is never written into an insecure file.
    if hasattr(os, "fchmod"):
        with contextlib.suppress(OSError):
            os.fchmod(fd, owner_only)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content + "\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    public_base_url = resolve_public_base_url(args.public_base_url)
    if not public_base_url:
        print(
            f"error: a public base URL is required (--public-base-url or "
            f"{ENV_OAUTH_PUBLIC_BASE_URL})",
            file=sys.stderr,
        )
        return 2

    client_id = args.client_id or f"static-{secrets.token_hex(8)}"
    client_secret = secrets.token_urlsafe(32)
    redirect_uris = tuple(args.redirect_uris)

    # Validate the whole static-client configuration via OAuthConfig so the
    # script rejects exactly what the server would reject at startup. The
    # client's granted scopes come from BRAMBLE_OAUTH_SCOPES at runtime, not
    # from this file, so this helper does not take a scope flag.
    try:
        OAuthConfig(
            public_base_url=public_base_url,
            static_client_id=client_id,
            static_client_secret=client_secret,
            static_client_redirect_uris=redirect_uris,
        )
    except (TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    env_block = render_env_block(client_id, client_secret, redirect_uris)

    # The client secret must never be echoed to stdout: stdout lands in shell
    # history, terminal scrollback and CI logs. It is written only to the
    # mode-600 env file, so --write is required. (Mirrors gen_admin_secret.py,
    # which writes the secret to a file rather than printing it.)
    if args.write is None:
        print(
            "error: refusing to print the client secret to stdout; pass "
            "--write PATH to write it to a mode-600 env file",
            file=sys.stderr,
        )
        return 2

    write_env_file(args.write, env_block)
    print(f"wrote static client config to {args.write} (mode 600)")
    print(f"  client_id:     {client_id}")
    print(f"  redirect_uris: {' '.join(redirect_uris)}")
    print(f"  client_secret: written to {args.write} - 'cat' it to read it")
    print(
        "\nNext: have the service load this env file (EnvironmentFile), restart "
        "bramble (seeds the client into oauth.db), then read the secret from "
        "the file and paste client_id + secret into the Claude connector.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
