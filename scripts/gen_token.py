"""Generate a bearer token for a project and store it in the token file.

Phase-3 Decision A helper. Each project (elder-berry, bramble, …) gets
its own token so tokens can be rotated or revoked individually.

Usage
-----

    python scripts/gen_token.py <project>
    python scripts/gen_token.py <project> --tokens-file /opt/bramble/secrets/tokens.json

The token-file path is resolved with the same priority as
:class:`bramble.server_config.ServerConfig`:

    CLI argument  >  ``BRAMBLE_TOKENS_FILE`` env var  >  ./secrets/tokens.json

The file (a JSON ``{project: token}`` map) and its parent directory are
created with owner-only permissions if they do not exist. Running the
script again for an existing project **rotates** that project's token.
The generated token is printed once to stdout – copy it into the MCP
configuration of the consuming AI tool.

Exit codes
----------
* ``0`` – token created or rotated.
* ``2`` – bad argument or an unreadable/malformed token file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

# Make ``src/`` importable when running this script directly without
# the package being installed (mirrors scripts/init_db.py).
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bramble.server_config import ENV_TOKENS_FILE  # noqa: E402  (sys.path setup above)

DEFAULT_TOKENS_FILE = ROOT / "secrets" / "tokens.json"

# Project identifiers follow the same kebab-case rule the MCP layer
# enforces; checking it here stops a typo'd project from ever being
# able to authenticate.
_KEBAB_CASE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# secrets.token_urlsafe(32) yields ~43 url-safe characters / 256 bits.
_TOKEN_NBYTES = 32


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "project",
        help="project identifier the token belongs to (kebab-case).",
    )
    parser.add_argument(
        "--tokens-file",
        type=Path,
        default=None,
        help=(
            f"path to the JSON token map "
            f"(env: {ENV_TOKENS_FILE}; default: {DEFAULT_TOKENS_FILE})"
        ),
    )
    return parser.parse_args(argv)


def resolve_tokens_file(
    cli_value: Path | None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Pick the token-file path per CLI > env > default."""

    if cli_value is not None:
        return cli_value
    environ: Mapping[str, str] = os.environ if env is None else env
    env_value = environ.get(ENV_TOKENS_FILE)
    if env_value:
        return Path(env_value)
    return DEFAULT_TOKENS_FILE


def load_tokens(path: Path) -> dict[str, str]:
    """Return the existing ``{project: token}`` map, or an empty one."""

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: cannot read token file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"error: token file {path} does not contain a JSON object")
    return data


def write_tokens(path: Path, tokens: dict[str, str]) -> None:
    """Atomically write the token map with owner-only permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)  # 0o700

    # Write to a sibling temp file, lock it down, then rename: a crash
    # mid-write never leaves a half-written or world-readable map.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(tokens, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    project: str = args.project
    if not _KEBAB_CASE_RE.match(project):
        print(
            f"error: project {project!r} must match kebab-case pattern "
            "^[a-z0-9][a-z0-9-]*$",
            file=sys.stderr,
        )
        return 2

    tokens_file = resolve_tokens_file(args.tokens_file)
    tokens = load_tokens(tokens_file)

    rotated = project in tokens
    token = secrets.token_urlsafe(_TOKEN_NBYTES)
    tokens[project] = token
    write_tokens(tokens_file, tokens)

    verb = "rotated" if rotated else "created"
    print(f"{verb} token for project {project!r} in {tokens_file}")
    print(f"  {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
