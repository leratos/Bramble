# Phase 3 – Deployment & Härtung

Status: **Entwurf – entscheidungsreif** (2026-05-18). Umsetzung noch
nicht begonnen.

Planungs-Entwurf. Die offenen Fragen wurden am 2026-05-18 mit dem
Nutzer geklärt und bestätigt – alle Designentscheidungen in Abschnitt 2
sind damit fix. Abschnitt 8 protokolliert die Entscheidungen.

## 1. Ziel von Phase 3

Bramble läuft als **öffentlich erreichbarer, abgesicherter Dienst** auf
`journal.last-strawberry.com` (Plesk/Ubuntu). Phase 2 hat den Server
lokal lauffähig gemacht; Phase 3 macht ihn deploybar und härtet ihn.
**Kein Daten-Import** und **kein Connector-Setup** – das ist Phase 4.

End-of-Phase-Kriterium:

* Server läuft als `systemd`-Service auf dem Ubuntu-Host unter
  `/opt/bramble`.
* Erreichbar über **HTTPS** via Nginx (Plesk-verwaltet); der FastMCP-
  Prozess selbst bindet nur an `127.0.0.1`.
* Jeder HTTP-MCP-Request braucht ein gültiges **projekt-eigenes
  Bearer-Token** – ohne Token kein Tool-Zugriff.
* Ein **Rate-Limit** greift pro Token bzw. IP.
* **Fail2Ban** sperrt IPs nach wiederholten Auth-Fehlern.
* `pytest` läuft weiterhin grün, ohne Netz-Zugriff (Auth- und
  Rate-Limit-Logik wird in-process getestet).

---

## 2. Designentscheidungen

### A) Auth: ein Bearer-Token pro Projekt
**Entschieden.** Jedes Projekt (elder-berry, bramble, bull-berry, …)
bekommt ein **eigenes** Bearer-Token. Damit lassen sich Tokens einzeln
rotieren/sperren, Rate-Limits trennen und Journal-Schreibzugriffe einem
Projekt zuordnen.

* **Token-Quelle: Datei.** Eine Token-Datei (Vorschlag:
  `/opt/bramble/secrets/tokens.json`, Modus `600`, **nicht** im Repo)
  mappt Projekt → Token im Klartext. Klartext bewusst: der Nutzer muss
  dieselben Token-Werte in andere KI-Tools übertragen können. Pfad über
  `BRAMBLE_TOKENS_FILE` (Env, mit CLI-Override – konsistent zur
  Phase-2-Discovery).
* **In-Memory-Vergleich.** `AuthValidator` lädt die Datei, hasht die
  Tokens (SHA-256) in eine Lookup-Map `hash → projekt`. Eingehendes
  Token wird gehasht und nachgeschlagen – kein zeichenweiser Vergleich,
  kein Timing-Leak.
* **Token-Generierung:** Helfer-Skript `scripts/gen_token.py` erzeugt
  via `secrets.token_urlsafe(32)` ein Token und trägt es für ein
  Projekt in die Datei ein.

### B) Token-Scope: schreiben nur ins eigene Projekt, lesen global
**Entschieden (bestätigt 2026-05-18).** Das Token identifiziert sein
Projekt.
`journal_append` wird im MCP-Layer abgewiesen, wenn das `project`-
Argument **nicht** dem Projekt des Tokens entspricht – so kann ein
geleaktes elder-berry-Token nichts in brambles Journal schreiben
(„Trennung"). `journal_read`, `journal_search` und
`journal_list_projects` bleiben **projektübergreifend**, weil
projektübergreifende Suche der Kern-Zweck von Bramble ist (siehe
README). Wenn auch Lesen eingeschränkt werden soll → eigene Entscheidung
nötig.

### C) Wo greift Auth – App-Layer vs. Nginx
**Entschieden.** Auth lebt im **App-Layer** (`AuthValidator`), nicht in
Nginx. Gründe: (1) der DI-Slot `auth_validator` ist im
`JournalMCPServer`-Konstruktor seit Phase 2 vorbereitet; (2) die
App-Schicht hat den Tool-/Projekt-Kontext für saubere, strukturierte
Logs, an die Fail2Ban andockt. Plesk-Nginx macht **nur** TLS-
Terminierung und Reverse-Proxy.

### D) FastMCP-eigene Auth vs. eigener Validator
**Zu verifizieren – erster Schritt der Phase.** FastMCP 3.x (installiert:
3.2.4) bringt evtl. eigene Auth-/Middleware-Primitive mit. **Vor** dem
Bau prüfen, welcher Aufhängepunkt existiert (analog zum „Demo-Tool
zuerst"-Schritt aus Phase 2, Abschnitt 6). Architektur-Intent bleibt:
eigener `AuthValidator`/`RateLimiter` an den vorhandenen DI-Slots –
FastMCP liefert höchstens den Aufhängepunkt.

### E) Rate-Limit
**Entschieden (bestätigt 2026-05-18).** Token-Bucket **in-memory**
(`RateLimiter`-Klasse, DI-Slot
`rate_limiter` vorbereitet). In-memory ist vertretbar – Single-Prozess-
Dienst; ein Neustart setzt die Buckets zurück, akzeptabel.

* **Pro Token:** 60 Requests/Minute (Bucket-Kapazität 60, Nachfüllung
  1/Sekunde). Großzügig für legitime KI-Nutzung, bremst ein geleaktes
  Token.
* **Pro IP:** 120 Requests/Minute als grober Backstop, bevor ein Token
  feststeht.

### F) Fail2Ban
**Entschieden.** `AuthValidator` loggt bei jedem fehlgeschlagenen
Auth-Versuch ein **definiertes JSON-Event** (`event: "auth_failed"`)
inkl. Client-IP. Der Fail2Ban-Filter matcht genau auf dieses Feld; das
JSON-Logging aus Phase 2 (`logging_setup`) liefert das Format bereits.

Politik (vom Nutzer vorgegeben, deckt sich mit dem bestehenden
Server-Setup):
* `maxretry = 3` – nach 3 Fehlversuchen Sperre.
* Erste Sperre **1 Stunde**, danach **eskalierend** (Fail2Ban
  `bantime.increment = true`) bis hin zu **dauerhaft**.

**Wichtig:** hinter Nginx ist die echte Client-IP nur via
`X-Forwarded-For` sichtbar – Nginx muss den Header setzen, die App ihm
nur von `127.0.0.1` vertrauen (siehe Risiken).

### G) TLS & Nginx über Plesk
**Entschieden.** Es gibt **keine** eigenständige vHost-Config im Repo.
TLS und der Basis-vHost werden von Plesk verwaltet (Let's Encrypt).
Bramble liefert nur ein Snippet mit **Nginx-Zusatzanweisungen**
(`deploy/plesk-nginx-directives.conf`), das in Plesks Feld „Additional
nginx directives" der Domain eingetragen wird: `proxy_pass` auf
`http://127.0.0.1:8765`, `X-Forwarded-For`/`X-Real-IP`-Header,
ggf. `location /mcp/`.

### H) systemd-Unit
**Entschieden.** Installationspfad `/opt/bramble`. Dedizierter
Service-User (kein root), `Restart=on-failure`. Die Unit setzt die
Env-Vars aus Phase 2 (`BRAMBLE_DB_PATH`, `BRAMBLE_TRANSPORT=http`,
`BRAMBLE_HOST=127.0.0.1`, `BRAMBLE_PORT`, `BRAMBLE_LOG_LEVEL`) plus die
neuen Phase-3-Vars (`BRAMBLE_TOKENS_FILE`, Rate-Limit-Parameter). Genau
dafür wurde die `CLI > Env > Default`-Discovery in Phase 2 gebaut.

### I) DB-Pfad & WAL-Mode
**Entschieden (bestätigt 2026-05-18).** DB unter
`/opt/bramble/data/bramble.db`. **WAL wird aktiviert** –
die MCP-Tools greifen über `asyncio.to_thread` real mehrthreadig auf
SQLite zu; WAL erlaubt parallele Leser neben einem Schreiber und
reduziert `SQLITE_BUSY`. Gesetzt einmalig in `JournalDB.initialize()`
(`PRAGMA journal_mode=WAL`, persistiert im File-Header). Damit schließt
Phase 3 eine offene Phase-1-Schuld. Konsequenz für Abschnitt J.

### J) Backup über bestehendes Borg-Script
**Entschieden.** Auf dem Server läuft bereits ein Borg-Backup-Script;
es wird **erweitert**, kein neues Backup-Tool. Wichtig wegen WAL
(Entscheidung I): eine reine File-Copy einer aktiven WAL-DB kann
inkonsistent sein. Das Script bekommt einen **Pre-Backup-Schritt**, der
einen konsistenten Snapshot zieht (`sqlite3 bramble.db ".backup
'<staging>'"` bzw. `VACUUM INTO`); Borg sichert dann den Snapshot, nicht
die Live-Datei. Die genauen Borg-Script-Änderungen kommen ins
Deployment-Runbook.

---

## 3. Klassen / Dateien (geplant)

| Datei | Klasse | Zweck |
|---|---|---|
| `src/bramble/auth_validator.py` | `AuthValidator` | Token-Datei laden, Token→Projekt auflösen, `auth_failed` loggen |
| `src/bramble/rate_limiter.py` | `RateLimiter` | Token-Bucket pro Token/IP |
| `src/bramble/journal_mcp_server.py` | `JournalMCPServer` | DI-Slots `auth_validator`/`rate_limiter` jetzt **konsumieren**; Token-Projekt-Bindung in `journal_append` |
| `src/bramble/server_config.py` | `ServerConfig` | neue Felder: `tokens_file`, Rate-Limit-Parameter |
| `scripts/gen_token.py` | – | Token erzeugen + in Token-Datei eintragen |
| `deploy/bramble.service` | – | systemd-Unit |
| `deploy/plesk-nginx-directives.conf` | – | Snippet für Plesks „Additional nginx directives" |
| `deploy/fail2ban/bramble-filter.conf` | – | Fail2Ban-Filter auf `auth_failed` |
| `deploy/fail2ban/bramble-jail.conf` | – | Jail: `maxretry=3`, eskalierende Sperre |
| `docs/deployment.md` | – | Runbook inkl. Borg-Script-Erweiterung |

**Konsequent „eine Klasse pro Datei"** – `AuthValidator` und
`RateLimiter` bekommen je eine eigene Datei, wie in Phase 1/2.
**Secrets** (`tokens.json`, Zertifikate) liegen nie im Repo –
`.gitignore` vor dem ersten Commit prüfen.

---

## 4. Request-Pfad: wie Auth & Rate-Limit eingehängt werden

Der `JournalMCPServer`-Konstruktor hat die Slots `auth_validator` und
`rate_limiter` seit Phase 2 (aktuell akzeptiert, aber nicht konsumiert –
`journal_mcp_server.py:89-101`). Phase 3 verdrahtet sie:

1. Eingehender HTTPS-Request → Nginx (Plesk, TLS) → FastMCP auf
   `127.0.0.1:8765`.
2. **Middleware-Schicht** (FastMCP-Mechanismus, Abschnitt 2D) ruft vor
   jedem Tool-Call:
   * `auth_validator` – Token gültig? Liefert das Projekt des Tokens.
   * `rate_limiter` – Budget für Token + IP übrig?
3. Bei Fehler: kein Tool-Call, Fehler zurück, `auth_failed`-Event
   geloggt (bei Auth-Fehler).
4. Bei Erfolg: weiter in den Phase-2-Tool-Pfad. **Neu:**
   `journal_append` prüft zusätzlich, dass `project` == Projekt des
   Tokens (Abschnitt 2B).

`JournalDB` und der Lese-/Such-Pfad der vier Tools bleiben unverändert –
Phase 3 fügt eine Schicht davor ein plus eine Scope-Prüfung im
Append-Tool. Der `stdio`-Transport bleibt **ohne Auth** (rein lokal).

---

## 5. Was bewusst NICHT in Phase 3 gehört

* **Import bestehender `journal.txt`-Dateien:** Phase 4.
* **Connector-Setup in Claude.ai / Claude Code:** Phase 4.
* **Migration der Projekt-System-Prompts:** Phase 5.
* **Volles RBAC / Rollen / Lese-Beschränkung pro Projekt:** nicht im
  Phasen-Plan. Phase 3 macht nur die *Schreib*-Bindung Token→Projekt
  (Abschnitt 2B), kein darüber hinausgehendes Rechte-System.
* **Web-UI / Admin-Oberfläche:** nicht vorgesehen.
* **Schema-Migrations-Tooling (alembic/yoyo):** offene Phase-1-Schuld,
  kein Phase-3-Ziel – nur erwähnen, nicht lösen.

---

## 6. Risiken & Schulden

* **FastMCP-Auth-/Middleware-API.** Größtes Risiko, wie in Phase 2. Vor
  dem Bau gegen 3.2.4 verifizieren (Abschnitt 2D). Nicht blind nach Doku
  bauen.
* **`X-Forwarded-For`-Spoofing.** Die App darf den Header **nur**
  akzeptieren, wenn der Request von `127.0.0.1` (Plesk-Nginx) kommt –
  sonst fälscht ein Angreifer seine IP und umgeht Fail2Ban.
* **Secrets im Repo.** `tokens.json`, `.env`, Zertifikate dürfen nicht
  eingecheckt werden.
* **In-memory Rate-Limit** geht bei jedem Neustart verloren – bewusst
  akzeptiert (Abschnitt 2E).
* **WAL + Borg.** Live-WAL-DB nicht per File-Copy sichern – Snapshot-
  Schritt im Borg-Script ist Pflicht (Abschnitt 2J).
* **Windows-Entwicklung, Linux-Deployment.** Deployment-Artefakte
  (`bramble.service`, Plesk-Snippet, Pfade) sind Linux-only und können
  lokal nicht voll getestet werden – Runbook + manuelle Verifikation auf
  dem Host einplanen.

---

## 7. Branch & Commit-Strategie

* Branch: `feature/phase-3-deployment`
* Commits in Etappen, nicht ein Mega-Commit:
  1. FastMCP-Auth-/Middleware-Spike (Verifikation, ggf. Wegwerf-Code)
  2. `AuthValidator` + Token-Datei-Laden + `gen_token.py` + Tests
  3. `RateLimiter` + Tests
  4. `ServerConfig`-Erweiterung (`tokens_file`, Rate-Limit) + Tests
  5. DI-Slots im `JournalMCPServer` verdrahten, Scope-Prüfung in
     `journal_append` + Tests
  6. `JournalDB.initialize()`: WAL-Mode + Test
  7. Deployment-Artefakte (`bramble.service`, Plesk-Snippet,
     Fail2Ban) + `docs/deployment.md` inkl. Borg-Erweiterung
  8. Manuelle Verifikation auf dem Host, Journal-Abschluss
* **Journal laufend pflegen** – explizite Lehre aus Phase 2 (journal.txt,
  Übergang Phase 2→3): nicht erst beim Phasen-Abschluss schreiben.
* Kein Push, kein PR durch Claude.

---

## 8. Geklärte Entscheidungen (2026-05-18)

| # | Frage | Klärung |
|---|---|---|
| 1 | Token-Strategie | **Pro Projekt ein eigenes Token** (Trennung, einzeln sperrbar). |
| 2 | Token-Quelle | **Datei** im Klartext (`tokens.json`), damit dieselben Tokens in anderen KI-Tools nutzbar sind. |
| 3 | Nginx unter Plesk | Plesk verwaltet vHost + TLS; Bramble liefert nur **Nginx-Zusatzanweisungen** als Snippet. |
| 4 | Pfade / Service-User | Installation unter **`/opt/bramble`**, dedizierter Service-User. |
| 5 | Rate-Limit-Werte | 60/min pro Token, 120/min pro IP. |
| 6 | Fail2Ban-Politik | `maxretry=3`, erste Sperre **1 h**, eskalierend bis **dauerhaft**. |
| 7 | Backup | Bestehendes **Borg-Script erweitern** (mit konsistentem SQLite-Snapshot). |
| 8 | WAL-Mode | Aktivieren, in `JournalDB.initialize()`. |
| 9 | Token-Scope | Schreiben projekt-gebunden, Lesen/Suchen global (Abschnitt 2B). |

Alle Punkte am 2026-05-18 bestätigt. Der Entwurf ist entscheidungsreif;
Umsetzung beginnt mit Abschnitt 7, Etappe 1.
