"""Owner-granted write authorization for an OAuth client (connector).

Phase 6.7. The OAuth path is read-only unless the resource owner, on the
consent screen, explicitly grants a connector write access to one named
project. That decision is stored per ``client_id`` (one DCR registration =
one connector) and is the single source of truth for OAuth write
authorization — the issued access token itself stays ``journal:read``, so a
client cannot escalate on its own; only the owner gate writes grants.

:class:`ClientGrant` is a small read-only value object; persistence lives in
:class:`bramble.oauth_store.OAuthStore`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClientGrant:
    """What the owner authorized for one OAuth ``client_id``.

    Parameters
    ----------
    client_id:
        The OAuth client (connector) the grant applies to.
    project:
        The single project the connector may write to, or ``None`` for a
        read-only grant.
    can_write:
        Whether write tools are permitted. ``True`` requires a ``project``.
    """

    client_id: str
    project: str | None
    can_write: bool
