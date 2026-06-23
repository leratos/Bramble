"""Bridge the existing static bearer tokens into FastMCP's auth layer.

Phase 6 puts the ``http`` transport behind FastMCP's native auth so the
self-hosted OAuth Authorization Server can protect ``/mcp`` and drive the
Claude Web/Mobile connector. The pre-existing local bearer path (a static
``project -> token`` map in ``tokens.json``) must keep working unchanged;
:class:`StaticTokenVerifier` is what makes that true.

It is a FastMCP :class:`TokenVerifier` that wraps the project's
:class:`~bramble.auth_validator.AuthValidator`. Combined with the OAuth
provider inside a ``MultiAuth`` (``MultiAuth(server=<oauth>,
verifiers=[StaticTokenVerifier(...)])``), a request to ``/mcp`` is accepted
if **either** the OAuth server **or** this static map recognises the
bearer token – so OAuth and the legacy static path coexist on one endpoint.

A recognised static token is mapped to an :class:`AccessToken` whose
``client_id`` encodes the bound project as ``static:<project>`` and whose
scopes include ``journal:write``; the MCP-layer middleware reads that to
restore the project write-scope binding and to allow writes (static tokens
are read-write, OAuth tokens are read-only). Validation is deliberately
**silent** (``AuthValidator.resolve_project``): ``MultiAuth`` may hand this
verifier a valid OAuth token, which it must simply decline without logging
an ``auth_failed`` event that would otherwise feed Fail2Ban.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp.server.auth.auth import TokenVerifier
from mcp.server.auth.provider import AccessToken

from bramble.auth_validator import AuthValidator

# Static (local) tokens are first-class operators of their project: unlike
# the read-only OAuth path, they keep the full read+write capability the
# Phase-3 bearer path always had.
_STATIC_SCOPES: tuple[str, ...] = ("journal:read", "journal:write")

# client_id convention the MCP-layer middleware parses back to a project.
STATIC_CLIENT_PREFIX = "static:"


class StaticTokenVerifier(TokenVerifier):
    """Verify a static ``tokens.json`` bearer and project its access.

    Parameters
    ----------
    auth_validator:
        The :class:`AuthValidator` holding the ``project -> token`` map.
    scopes:
        Scopes granted to a recognised static token. Defaults to read +
        write, preserving the legacy bearer path's full capability.
    """

    def __init__(
        self,
        auth_validator: AuthValidator,
        *,
        scopes: Sequence[str] = _STATIC_SCOPES,
    ) -> None:
        super().__init__()
        if not isinstance(auth_validator, AuthValidator):
            raise TypeError("auth_validator must be an AuthValidator")
        self._auth_validator = auth_validator
        self._scopes = list(scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an :class:`AccessToken` for a known static token, else None."""

        project = self._auth_validator.resolve_project(token)  # silent
        if project is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"{STATIC_CLIENT_PREFIX}{project}",
            scopes=list(self._scopes),
            expires_at=None,
        )
