# Phase 6 – Self-hosted OAuth 2.1 Authorization Server

## Anlass

Bramble soll als **privater Custom-Connector** in Claude Web **und** Claude
Mobile einbindbar sein. Diese Clients verbinden sich aus Anthropics Cloud und
unterstützen in der Connector-UI **nur OAuth** (Client-ID/Secret bzw. Dynamic
Client Registration) – statische Bearer-Tokens oder Custom-Header gehen dort
nicht. Die Auth-Grundsatzentscheidung (Option A: echtes OAuth) wurde in
`bramble#658` getroffen und bis zur IT/Compliance-Klärung zurückgestellt.

Phase 6 baut den technischen Unterbau: einen self-hosted OAuth-2.1-Authorization-
Server (AS) plus Resource-Server-Schutz für den `/mcp`-Endpoint. Der **Go-Live**
(echte Datenexposition) bleibt hinter dem Compliance-Gate (Entscheidung D1).

## Untersuchung (FastMCP 3.2.4)

- `OAuthProvider` (FastMCP) ist ein vollständiger Self-Hosted-AS:
  `authorize`, `exchange_authorization_code`, `exchange_refresh_token`,
  `register_client` (DCR), `revoke_token`, `load_*`, `verify_token`, plus
  Routen-Mounting (`get_routes`, `get_well_known_routes`, `set_mcp_path`).
  `FastMCP(auth=provider)` montiert Discovery, `/authorize`, `/token`,
  `/register`, `/revoke` und beide well-known-Dokumente **am Host-Root**.
- PKCE **S256** erzwingt der MCP-SDK-`TokenHandler` vor dem Code-Exchange
  (`code_challenge_methods_supported: ["S256"]`). Kein eigener Code nötig.
- DCR (RFC 7591) per `ClientRegistrationOptions(enabled=True)`.
- `MultiAuth(server=…, verifiers=[…])` erlaubt Koexistenz mehrerer Auth-Wege.
- `InMemoryOAuthProvider` nutzt **opake** Tokens (Dict-Lookup, kein JWT-Signing).

## Entscheidungen

- **D1 – Bau jetzt, Go-Live gated.** Der Bau ist reversibel und schaltet nichts
  scharf (`BRAMBLE_ENABLE_OAUTH=false` als Default in der systemd-Unit). Der
  Connector geht erst nach IT/Compliance-Freigabe (`bramble#658`, Punkte 1–5)
  live.
- **D2 – Phase 6, nicht „4f“.** Die Nummer 4f ist mit der Open-Items-Resolution
  (`bramble#635–639`, `phase-4f-open-items-resolution.md`) belegt.
- **D3 – OAuth-Pfad read-only.** Default-Scope `journal:read`; `journal_append`
  /`journal_resolve` werden für OAuth-Prinzipale abgelehnt. `bramble#658` hält
  fest, dass Schreiben über diesen Zugang nicht benötigt wird. Statische Tokens
  behalten Schreibrechte auf ihr Projekt.
- **D4 – Eigene `oauth.db`, opake Tokens.** Der mutable OAuth-State (Clients,
  Auth-Codes, Tokens) liegt in einer **separaten** SQLite-Datei, getrennt von
  der append-only `bramble.db` (FTS5, Borg-Snapshot). Opake, im Store
  persistierte Tokens → **kein Signing-Key**, Revocation = Row-Delete. Einziges
  optionales Secret: das Secret des statischen Fallback-Clients.

## Architektur (Koexistenz – „lokaler Bearer-Pfad darf nicht brechen“)

- **stdio** (Claude Desktop/Code lokal) bleibt komplett ohne HTTP-Auth –
  unberührt.
- **http** ohne OAuth (`enable_oauth=false`) = exakt der Phase-3-Static-Bearer-
  Pfad (`_AuthRateLimitMiddleware`, byte-for-byte unverändert).
- **http** mit OAuth: `FastMCP(auth=MultiAuth(server=BrambleOAuthProvider,
  verifiers=[StaticTokenVerifier]))`. Ein `/mcp`-Request wird akzeptiert, wenn
  **entweder** der OAuth-AS **oder** der Static-Verifier den Bearer kennt. Die
  ASGI-Auth-Schicht gated `/mcp` und liefert für unauthenticated Requests
  `401` + `WWW-Authenticate … resource_metadata="…/.well-known/oauth-protected-
  resource/mcp"` – genau das, was Claudes OAuth-Discovery braucht.
- `_PrincipalRateLimitMiddleware` (Tool-Layer) liest den von der ASGI-Schicht
  validierten Prinzipal (`get_access_token()`) und erzwingt die Bramble-Policy:
  Rate-Limit (per-IP + per-Prinzipal), Read-only-Gate (`journal:write` nötig für
  Write-Tools) und die Projekt-Bindung statischer Tokens
  (`client_id == "static:<project>"`). Ohne Prinzipal wird abgelehnt (der
  In-Process-Client umgeht die ASGI-Auth – Defense-in-Depth).

## Komponenten

| Datei | Klasse / Zweck |
| --- | --- |
| `src/bramble/oauth_config.py` | `OAuthConfig` – Public-URL, Scopes, DCR, TTLs, oauth.db-Pfad, optionaler statischer Client; `from_env`. |
| `src/bramble/oauth_store.py` | `OAuthStore` – SQLite-Persistenz (Clients/Codes/Access/Refresh), WAL, TTL-Purge, Refresh↔Access-Pairing. |
| `src/bramble/oauth_provider.py` | `BrambleOAuthProvider(OAuthProvider)` – store-backed AS-Logik. |
| `src/bramble/static_token_verifier.py` | `StaticTokenVerifier(TokenVerifier)` – `tokens.json`-Bearer → `AccessToken`, silent. |
| `src/bramble/server_config.py` | `enable_oauth`-Master-Switch (CLI/Env). |
| `src/bramble/journal_mcp_server.py` | `auth_provider`-Modus + `_PrincipalRateLimitMiddleware`. |
| `src/bramble/__main__.py` | http-OAuth-Stack (MultiAuth) + optionales Seeding des statischen Clients. |
| `scripts/gen_oauth_client.py` | erzeugt die Credentials des statischen Fallback-Clients (Env-Block). |

Zwei bewusste Abweichungen vom In-Memory-Referenzprovider:

1. Auth-Code-Verbrauch per atomarem `DELETE`-mit-Rowcount → echte Single-Use-
   Semantik auch bei gleichzeitigen Token-Requests.
2. Ein **ablaufender Access-Token** wird allein gelöscht; sein Refresh-Token
   bleibt erhalten (sonst wäre der Refresh-Flow kaputt). Nur explizites
   `revoke_token` kaskadiert auf den Partner.

## Endpoints / Discovery (am Root)

- `GET /.well-known/oauth-authorization-server` (RFC 8414)
- `GET /.well-known/oauth-protected-resource/mcp` (RFC 9728 – **pfad-suffixiert**)
- `GET|POST /authorize`, `POST /token`, `POST /register`, `POST /revoke`

## Deployment

- **nginx** (`deploy/plesk-nginx-directives.conf`): neue Passthrough-Blöcke für
  `^~ /.well-known/oauth-` (ACME-`acme-challenge` bleibt unberührt) und
  `~ ^/(authorize|token|register|revoke)$`, jeweils → `127.0.0.1:8765` mit
  Pfad-Erhalt. `/mcp` unverändert.
- **systemd** (`deploy/bramble.service`): `BRAMBLE_ENABLE_OAUTH=false` (Default),
  `BRAMBLE_OAUTH_PUBLIC_BASE_URL`, `BRAMBLE_OAUTH_DB_PATH`, optionales
  `EnvironmentFile=-/opt/bramble/secrets/oauth.env`. `oauth.db` liegt in
  `/opt/bramble/data` (von `ReadWritePaths` gedeckt).
- **Secrets**: das Secret des statischen Clients nie ins Repo; via
  `gen_oauth_client.py` → `oauth.env` (Mode 600). `__main__` seedet den Client
  beim Start idempotent aus diesen Env-Vars.

## Fail2Ban / Restrisiko

In OAuth-Modus wird `auth_failed` **nicht** mehr emittiert (Static-Verifier ist
silent, fehlerhafte Bearer werden an der ASGI-Schicht mit `401` abgewiesen). Das
bestehende `auth_failed`-Jail greift dann nicht. **Kein** Jail auf `/mcp`-401
bauen: ein unauthenticated `/mcp`-401 ist der erste, legitime Discovery-Schritt
von Claude – ein Ban dort sperrt echte Clients aus. Die AS-Endpunkte
(`/authorize`, `/token`, `/register`) sind die eigentliche Brute-Force-Fläche;
bei Auffälligkeiten nach Go-Live ein **getuntes** Jail mit großzügigem
`maxretry` ergänzen (legitime Clients retrien ebenfalls). Codes sind single-use
und kurzlebig. Siehe `deploy/fail2ban/bramble-jail.conf`.

## Tests

- Unit: `OAuthConfig`, `OAuthStore` (CRUD/Pairing/TTL-Purge), `BrambleOAuthProvider`
  (PKCE-Persistenz, Single-Use, Refresh-Rotation + Scope-Eskalation,
  Access-Expiry-behält-Refresh, Revocation-Kaskade), `StaticTokenVerifier`
  (silent), `resolve_project`, `_PrincipalRateLimitMiddleware._authorize`.
- Integration: `MultiAuth.verify_token` akzeptiert OAuth- **und** Static-Token,
  lehnt Müll/revoked ab; Discovery-JSON-Shape (S256); `/mcp`→401+resource_metadata.
- Regression: Static-Pfad unverändert; die 6 PEP-562-Lazy-Load-Tests bleiben
  grün (OAuth-Module nicht im Eager-Import-Pfad).

## Definition of Done

- [x] Beide well-known-Endpoints liefern spec-konformes JSON (Test).
- [x] Lokaler/Static-Bearer-Pfad unverändert (Regression grün).
- [x] nginx/systemd/fail2ban-Änderungen im Repo.
- [ ] Connector real in Claude Web verbinden (echter OAuth-Flow) – Go-Live.
- [ ] `journal_context` über den Web-Connector liefert echte Einträge – Go-Live.

## Restrisiko / offen

- Der Transport-Nahtpunkt „ASGI setzt den Prinzipal, den die Tool-Middleware
  liest“ ist Framework-Glue (über `MultiAuth.verify_token` und die
  Middleware-Unit-Tests separat abgedeckt) und wird beim echten Claude-Connect
  endgültig verifiziert.
- Compliance-Gate `bramble#658` (Punkte 1–5) ist Voraussetzung für den Go-Live.
- DeutschlandGPT als Remote-MCP-Client ist weiterhin unbestätigt (Show-Stopper-
  Check aus `bramble#658`).
