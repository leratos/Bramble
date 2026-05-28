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
import os
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
from bramble.token_store import (  # noqa: E402  (sys.path setup above)
    TokenStore,
    load_token_map,
    validate_project,
    write_token_map,
)

DEFAULT_TOKENS_FILE = ROOT / "secrets" / "tokens.json"

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

    try:
        return load_token_map(path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: cannot read token file {path}: {exc}") from exc


def write_tokens(path: Path, tokens: dict[str, str]) -> None:
    """Atomically write the token map with owner-only permissions."""

    write_token_map(path, tokens)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    project: str = args.project
    try:
        project = validate_project(project)
    except ValueError as exc:
        print(
            f"error: {exc}",
            file=sys.stderr,
        )
        return 2

    tokens_file = resolve_tokens_file(args.tokens_file)
    try:
        mutation = TokenStore(tokens_file).upsert(project)
    except (OSError, ValueError) as exc:
        print(f"error: cannot update token file {tokens_file}: {exc}", file=sys.stderr)
        return 2

    print(f"{mutation.action} token for project {project!r} in {tokens_file}")
    print(f"  {mutation.token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
