"""CLI entry point for the Bramble MCP server.

Wires the building blocks together:

* :class:`bramble.server_config.ServerConfig` resolves CLI / env /
  default values.
* :func:`bramble.logging_setup.configure_logging` installs the JSON
  log handler on stderr.
* :class:`bramble.journal_db.JournalDB` is opened and initialised.
* For the ``http`` transport, :class:`bramble.auth_validator.AuthValidator`
  and :class:`bramble.rate_limiter.RateLimiter` are built and handed
  to the server so every request is gated by a bearer token and a
  rate limit. ``stdio`` is local and runs without that gate.
* :class:`bramble.journal_mcp_server.JournalMCPServer` is constructed
  with that DB and started on the configured transport.

Invoked via ``python -m bramble`` or the ``bramble-server`` console
script defined in :file:`pyproject.toml`.
"""

from __future__ import annotations

import logging

from bramble.auth_validator import AuthValidator
from bramble.journal_db import JournalDB
from bramble.journal_mcp_server import JournalMCPServer
from bramble.logging_setup import configure_logging
from bramble.rate_limiter import RateLimiter
from bramble.server_config import ServerConfig
from bramble.token_store import load_token_map

logger = logging.getLogger(__name__)


def main() -> None:
    """Resolve configuration, prepare the DB, and start serving."""

    config = ServerConfig.from_sources()
    configure_logging(config.log_level)

    db = JournalDB(config.db_path)
    db.initialize()
    logger.info(
        "bramble-server starting",
        extra={
            "db_path": str(config.db_path),
            "transport": config.transport,
        },
    )

    if config.transport == "stdio":
        server = JournalMCPServer(db)
        server.run(transport="stdio")
    else:
        db.register_projects(load_token_map(config.tokens_file).keys())
        rate_limiter = RateLimiter(
            per_token_rpm=config.rate_limit_per_token,
            per_ip_rpm=config.rate_limit_per_ip,
        )
        if config.enable_oauth:
            # OAuth mode: the self-hosted Authorization Server protects /mcp
            # and a static-token verifier inside the same MultiAuth keeps the
            # legacy bearer path working (Phase-6 decision D3/D4). The owner
            # gate (Phase 6.6) authenticates the resource owner on /authorize.
            auth_provider, http_middleware, grant_store, allow_write = (
                _build_oauth_stack(config)
            )
            server = JournalMCPServer(
                db,
                auth_provider=auth_provider,
                rate_limiter=rate_limiter,
                http_middleware=http_middleware,
                oauth_grant_store=grant_store,
                oauth_allow_write=allow_write,
            )
            logger.info("OAuth authorization server enabled for http transport")
        else:
            auth_validator = AuthValidator(config.tokens_file)
            server = JournalMCPServer(
                db, auth_validator=auth_validator, rate_limiter=rate_limiter
            )
        server.run(transport="http", host=config.host, port=config.port)


def _build_oauth_stack(config: ServerConfig):
    """Build the Phase-6 OAuth auth provider, http gate middleware and store.

    Returns ``(auth_provider, http_middleware, grant_store)`` where
    ``auth_provider`` is a ``MultiAuth`` (self-hosted AS + static-token
    verifier), ``http_middleware`` is the resource-owner login/consent gate on
    ``/authorize`` (Phase 6.6/6.7), and ``grant_store`` is the ``OAuthStore``
    the MCP-layer middleware consults for owner write grants. OAuth-specific
    modules are imported lazily so the common stdio / static-http paths never
    pull them in.
    ``OAuthConfig.from_env`` raises if the required public base URL is missing,
    and the owner gate raises if the owner secret file is absent — both the
    right fail-fast behaviour once OAuth has been switched on.
    """

    from fastmcp.server.auth.auth import MultiAuth
    from mcp.shared.auth import OAuthClientInformationFull

    from bramble.oauth_config import OAuthConfig
    from bramble.oauth_owner_gate import build_owner_gate
    from bramble.oauth_provider import BrambleOAuthProvider
    from bramble.oauth_store import OAuthStore
    from bramble.static_token_verifier import StaticTokenVerifier

    oauth_config = OAuthConfig.from_env()
    store = OAuthStore(oauth_config.oauth_db_path)
    store.initialize()

    # Seed the optional confidential static fallback client (declared in the
    # secrets env file) so it survives a recreated oauth.db. Idempotent upsert;
    # only runs when a static client is configured (DCR-only otherwise).
    if oauth_config.has_static_client:
        store.save_client(
            OAuthClientInformationFull(
                client_id=oauth_config.static_client_id,
                client_secret=oauth_config.static_client_secret,
                redirect_uris=list(oauth_config.static_client_redirect_uris),
                scope=" ".join(oauth_config.scopes),
                grant_types=["authorization_code", "refresh_token"],
                token_endpoint_auth_method="client_secret_post",
            )
        )
        logger.info(
            "seeded static oauth client", extra={"client_id": oauth_config.static_client_id}
        )

    provider = BrambleOAuthProvider(store=store, config=oauth_config)
    # The static-token verifier is only the backwards-compatibility path. On a
    # DCR-only deployment there may be no tokens file, and AuthValidator would
    # raise FileNotFoundError; skip the verifier in that case so OAuth-only
    # deployments start (load_token_map already tolerates the absence).
    verifiers = []
    if config.tokens_file.exists():
        verifiers.append(StaticTokenVerifier(AuthValidator(config.tokens_file)))
    else:
        logger.info(
            "no tokens file at %s; running OAuth-only (no static verifier)",
            config.tokens_file,
        )
    logger.info(
        "oauth config",
        extra={
            "public_base_url": oauth_config.public_base_url,
            "oauth_db_path": str(oauth_config.oauth_db_path),
            "enable_dcr": oauth_config.enable_dcr,
            "has_static_client": oauth_config.has_static_client,
        },
    )
    auth_provider = MultiAuth(
        server=provider,
        verifiers=verifiers,
        base_url=oauth_config.public_base_url,
    )
    owner_gate = build_owner_gate(oauth_config, store)
    return auth_provider, [owner_gate], store, oauth_config.allow_oauth_write


if __name__ == "__main__":
    main()
