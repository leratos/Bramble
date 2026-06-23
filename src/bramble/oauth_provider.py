"""Bramble's persistent OAuth 2.1 Authorization Server provider.

:class:`BrambleOAuthProvider` is a FastMCP :class:`OAuthProvider` whose
state lives in a SQLite :class:`~bramble.oauth_store.OAuthStore` instead of
in process memory. It is modelled on FastMCP's reference
``InMemoryOAuthProvider`` but made durable and configurable; FastMCP mounts
the discovery, ``/authorize``, ``/token``, ``/register`` and ``/revoke``
routes automatically when this provider is passed to ``FastMCP(auth=...)``.

Tokens are **opaque** random strings (no JWT signing key, Phase-6 decision
D4): an access token is valid iff it is present and unexpired in the store,
so revocation is just a row delete. The framework's ``TokenHandler``
enforces PKCE (S256) before calling :meth:`exchange_authorization_code`, so
that verification is not re-implemented here.

Two deliberate deviations from the in-memory reference:

* The authorization code is consumed with an atomic ``DELETE`` that reports
  whether a row went, giving genuine single-use semantics even if two token
  requests race – instead of a check-then-delete on a dict.
* When an **access** token merely *expires* it is removed on its own; the
  paired refresh token is left intact (the reference deletes both, which
  would defeat the refresh flow). Only an explicit ``revoke_token`` cascades
  to the counterpart.

All store access is synchronous SQLite, wrapped in
:func:`asyncio.to_thread` because the provider methods run on the server's
event loop.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable

from fastmcp.server.auth.auth import (
    ClientRegistrationOptions,
    OAuthProvider,
    RevocationOptions,
)
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from bramble.oauth_config import OAuthConfig, validate_redirect_uri
from bramble.oauth_store import OAuthStore
from bramble.static_token_verifier import STATIC_CLIENT_PREFIX

logger = logging.getLogger(__name__)


class BrambleOAuthProvider(OAuthProvider):
    """Durable, read-only-oriented OAuth 2.1 AS backed by :class:`OAuthStore`.

    Parameters
    ----------
    store:
        Initialised :class:`OAuthStore` for clients/codes/tokens.
    config:
        :class:`OAuthConfig` supplying the public base URL, advertised
        scopes, DCR toggle and token/code TTLs.
    time_source:
        Callable returning epoch seconds. Injected so token expiry is
        deterministic in tests; defaults to :func:`time.time`.
    """

    def __init__(
        self,
        *,
        store: OAuthStore,
        config: OAuthConfig,
        time_source: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(store, OAuthStore):
            raise TypeError("store must be an OAuthStore")
        if not isinstance(config, OAuthConfig):
            raise TypeError("config must be an OAuthConfig")

        super().__init__(
            base_url=config.public_base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=config.enable_dcr,
                valid_scopes=list(config.scopes),
                default_scopes=list(config.scopes),
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=list(config.scopes),
        )
        self._store = store
        self._config = config
        self._now = time_source

    # ------------------------------------------------------------------
    # Clients (RFC 7591 dynamic registration + static fallback)
    # ------------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await asyncio.to_thread(self._store.get_client, client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Validate requested scopes against the configured valid set, matching
        # the MCP SDK / reference behaviour.
        if (
            client_info.scope is not None
            and self.client_registration_options is not None
            and self.client_registration_options.valid_scopes is not None
        ):
            requested = set(client_info.scope.split())
            valid = set(self.client_registration_options.valid_scopes)
            invalid = requested - valid
            if invalid:
                # RegistrationError (not ValueError) so the SDK's
                # RegistrationHandler returns an OAuth 400, not a 500.
                raise RegistrationError(
                    error="invalid_client_metadata",
                    error_description=(
                        f"Requested scopes are not valid: {', '.join(sorted(invalid))}"
                    ),
                )
        if client_info.client_id is None:
            raise ValueError("client_id is required for client registration")
        # Defence in depth: never let a client carry the reserved static
        # principal prefix (the MCP-layer middleware trusts it for the legacy
        # bearer path). DCR ids are server-generated, so this only ever guards
        # against a misconfigured / hostile id.
        if client_info.client_id.startswith(STATIC_CLIENT_PREFIX):
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description=(
                    f"client_id must not use the reserved "
                    f"{STATIC_CLIENT_PREFIX!r} prefix"
                ),
            )
        # DCR redirect_uris come from the unauthenticated /register request.
        # Apply the same https/loopback policy as the static client, so a code
        # is never sent to a cleartext or custom-scheme callback. Translate the
        # ValueError into a RegistrationError -> proper OAuth 400.
        for uri in client_info.redirect_uris or []:
            try:
                validate_redirect_uri(str(uri))
            except ValueError as exc:
                raise RegistrationError(
                    error="invalid_redirect_uri", error_description=str(exc)
                ) from exc
        await asyncio.to_thread(self._store.save_client, client_info)
        logger.info("registered oauth client %s", client_info.client_id)

    # ------------------------------------------------------------------
    # Authorization codes
    # ------------------------------------------------------------------
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Issue an authorization code and return the client redirect URI."""

        registered = await asyncio.to_thread(self._store.get_client, client.client_id)
        if registered is None:
            raise AuthorizeError(
                error="unauthorized_client",
                error_description=f"Client '{client.client_id}' not registered.",
            )
        if client.client_id is None:
            raise AuthorizeError(
                error="invalid_client", error_description="Client ID is required"
            )

        # Bind the authorization to this server's resource (RFC 8707). Reject a
        # request that targets a different resource, so this AS cannot be used
        # as a token factory for another MCP server (confused-deputy): a token
        # issued here is always for Bramble's own /mcp.
        resource = self._config.resource_url
        if (
            params.resource is not None
            and str(params.resource).rstrip("/") != resource.rstrip("/")
        ):
            raise AuthorizeError(
                error="invalid_request",
                error_description="resource does not match this server",
            )

        # Narrow requested scopes to those the client is registered for. When
        # the request omits scope entirely (params.scopes is None), fall back
        # to the client's registered scope (else the configured default) –
        # an empty-scope token would be rejected by the resource and the
        # client could authorize but call nothing.
        if params.scopes is not None:
            scopes_list = list(params.scopes)
        elif client.scope:
            scopes_list = client.scope.split()
        else:
            scopes_list = list(self._config.scopes)
        if client.scope:
            allowed = set(client.scope.split())
            scopes_list = [s for s in scopes_list if s in allowed]

        code_value = self._new_token()
        auth_code = AuthorizationCode(
            code=code_value,
            client_id=client.client_id,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=scopes_list,
            expires_at=self._now() + self._config.auth_code_ttl,
            code_challenge=params.code_challenge,
            resource=resource,
        )
        await asyncio.to_thread(self._store.save_auth_code, auth_code)
        return construct_redirect_uri(
            str(params.redirect_uri), code=code_value, state=params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        obj = await asyncio.to_thread(self._store.get_auth_code, authorization_code)
        if obj is None:
            return None
        if obj.client_id != client.client_id:
            return None  # Belongs to a different client.
        if obj.expires_at < self._now():
            await asyncio.to_thread(self._store.delete_auth_code, authorization_code)
            return None
        return obj

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Consume atomically: a successful delete is proof the code was still
        # unused. If it returns False the code was already spent (or gone), so
        # the grant is invalid – this closes the single-use race.
        consumed = await asyncio.to_thread(
            self._store.delete_auth_code, authorization_code.code
        )
        if not consumed:
            raise TokenError(
                "invalid_grant", "Authorization code not found or already used."
            )
        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        return await self._issue_token_pair(
            client.client_id, authorization_code.scopes
        )

    # ------------------------------------------------------------------
    # Refresh tokens
    # ------------------------------------------------------------------
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        obj = await asyncio.to_thread(self._store.get_refresh_token, refresh_token)
        if obj is None:
            return None
        if obj.client_id != client.client_id:
            return None
        if obj.expires_at is not None and obj.expires_at < self._now():
            await self._revoke_refresh(obj.token)  # Expired: drop the whole grant.
            return None
        return obj

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Requested scopes must not exceed those the refresh token carries.
        if not set(scopes).issubset(set(refresh_token.scopes)):
            raise TokenError(
                "invalid_scope",
                "Requested scopes exceed those authorized by the refresh token.",
            )
        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        # Rotate atomically: consume the presented refresh token with a delete
        # that reports whether a row went. If it did not, the token was already
        # used (or a concurrent refresh rotated it), so the grant is invalid –
        # this stops two concurrent /token refreshes from both minting a new
        # pair from one refresh token. Mirrors the single-use auth-code consume.
        paired_access = await asyncio.to_thread(
            self._store.get_paired_access_token, refresh_token.token
        )
        consumed = await asyncio.to_thread(
            self._store.delete_refresh_token, refresh_token.token
        )
        if not consumed:
            raise TokenError(
                "invalid_grant", "Refresh token not found or already used."
            )
        if paired_access:
            await asyncio.to_thread(self._store.delete_access_token, paired_access)
        granted = scopes if scopes else list(refresh_token.scopes)
        return await self._issue_token_pair(client.client_id, granted)

    # ------------------------------------------------------------------
    # Access tokens
    # ------------------------------------------------------------------
    async def load_access_token(self, token: str) -> AccessToken | None:
        obj = await asyncio.to_thread(self._store.get_access_token, token)
        if obj is None:
            return None
        if obj.expires_at is not None and obj.expires_at < self._now():
            # Drop ONLY the expired access token; the refresh token stays so
            # the client can mint a fresh access token (deliberate deviation
            # from the in-memory reference, which deletes both).
            await asyncio.to_thread(self._store.delete_access_token, token)
            return None
        return obj

    async def verify_token(self, token: str) -> AccessToken | None:
        # TokenVerifier protocol: delegate to the expiry-aware loader.
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke a token and its counterpart (RFC 7009 cascade)."""

        if isinstance(token, AccessToken):
            await self._revoke_access(token.token)
        elif isinstance(token, RefreshToken):
            await self._revoke_refresh(token.token)
        # Unknown/already-gone token: nothing to do.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _issue_token_pair(
        self, client_id: str, scopes: list[str]
    ) -> OAuthToken:
        access_value = self._new_token()
        refresh_value = self._new_token()
        now = self._now()
        access_expires = int(now + self._config.access_token_ttl)
        refresh_expires = (
            None
            if self._config.refresh_token_ttl is None
            else int(now + self._config.refresh_token_ttl)
        )

        await asyncio.to_thread(
            self._store.save_access_token,
            AccessToken(
                token=access_value,
                client_id=client_id,
                scopes=scopes,
                expires_at=access_expires,
                resource=self._config.resource_url,
            ),
        )
        await asyncio.to_thread(
            self._store.save_refresh_token,
            RefreshToken(
                token=refresh_value,
                client_id=client_id,
                scopes=scopes,
                expires_at=refresh_expires,
            ),
            access_token=access_value,
        )
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=self._config.access_token_ttl,
            refresh_token=refresh_value,
            scope=" ".join(scopes),
        )

    async def _revoke_access(self, access_str: str) -> None:
        refresh_str = await asyncio.to_thread(
            self._store.get_refresh_for_access, access_str
        )
        await asyncio.to_thread(self._store.delete_access_token, access_str)
        if refresh_str:
            await asyncio.to_thread(self._store.delete_refresh_token, refresh_str)

    async def _revoke_refresh(self, refresh_str: str) -> None:
        access_str = await asyncio.to_thread(
            self._store.get_paired_access_token, refresh_str
        )
        await asyncio.to_thread(self._store.delete_refresh_token, refresh_str)
        if access_str:
            await asyncio.to_thread(self._store.delete_access_token, access_str)

    @staticmethod
    def _new_token() -> str:
        """Return a fresh opaque, URL-safe token (256 bits of entropy)."""

        return secrets.token_urlsafe(32)
