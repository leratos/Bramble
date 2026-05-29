# Bramble

Self-hosted MCP-Server für ein projektübergreifendes Entwicklungs-Journal.

Bramble ist der zentrale "Brombeerstrauch", an dem alle Beeren-Projekte
(Elder-Berry, Bull-Berry, Berry-Gym, Last-Strawberry, ...) hängen.
Statt in jedem Repo eine eigene `docs/journal.txt` zu pflegen, schreiben
alle Projekte ihre Journal-Einträge über das Model-Context-Protocol an
einen gemeinsamen Server, der die Daten in einer SQLite-Datenbank
ablegt und projektübergreifend durchsuchbar macht.

## Status

In aktiver Entwicklung. **Phase 3 – Deployment & Härtung** ist
abgeschlossen: Bramble läuft als systemd-Service hinter Plesk/Nginx auf
`journal.last-strawberry.com`, mit Bearer-Token-Auth, Rate-Limit,
Fail2Ban und WAL-sicherem SQLite-Betrieb. Das Borg-Backup inklusive
Restore-Test ist verifiziert.

**Phase 4d (Kontexttools)** ist abgeschlossen und hostseitig im
read-only Smoke verifiziert. Aktueller Fokus ist **Phase 4e**:
verbindliche Journal-Workflows und operativer Rollout fuer Admin-UI und
Agenten.

Ab Phase 4 ist Brambles aktives Projektgedaechtnis das MCP-Journal
selbst. `docs/journal.txt` bleibt nur als historische Importquelle im
Repo und wird nicht mehr fuer neue Eintraege verwendet.

## Phasen-Plan

1. **Phase 1** – Repo-Setup, DB-Schema, Core-Klassen (JournalEntry,
   JournalDB) mit Unit-Tests. ✅
2. **Phase 2** – FastMCP-Server, vier MCP-Tools, CLI-Entry-Point,
   lokal lauffähig. ✅
3. **Phase 3** – Deployment auf `journal.last-strawberry.com`
   (Plesk/Ubuntu), systemd, Nginx-Reverse-Proxy, Bearer-Token-Auth,
   Rate-Limit, Fail2Ban. ✅
4. **Phase 4** – Import bestehender `journal.txt`-Dateien,
    Connector-Setup in Claude.ai und Claude Code. ✅
5. **Phase 5** – Migration aller Projekt-System-Prompts auf die
   MCP-Tools.

## Architektur

- Python 3.12, SQLite (FTS5 für Volltextsuche), FastMCP 3.x
- OOP, eine Klasse pro Datei
- Dependency Injection über den Konstruktor
- Append-only: keine `update`/`delete`-Tools – Korrekturen erfolgen
  über neue Einträge

### Datenmodell

```sql
CREATE TABLE journal_entries (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    project   TEXT NOT NULL,
    timestamp TEXT NOT NULL,        -- ISO 8601, UTC
    status    TEXT NOT NULL,        -- in_arbeit | abgeschlossen | notiz | bugfix
    phase     TEXT,
    title     TEXT,
    content   TEXT NOT NULL
);
```

Zusätzlich: Index `idx_project_ts` für schnelle `read`-Queries und
eine FTS5-Tabelle `journal_fts` (indiziert `content` und `title`),
synchronisiert über drei Trigger (insert/update/delete).

### MCP-Tools

Acht Tools auf dem `JournalMCPServer`, jedes mit `ToolError`-konformer
Fehlerübersetzung:

| Tool | Zweck |
|---|---|
| `journal_read(project, n=80)` | Neueste `n` Einträge für ein Projekt, neueste zuerst |
| `journal_append(project, status, content, phase=None, title=None)` | Neuen Eintrag schreiben; Timestamp wird serverseitig gesetzt |
| `journal_search(project, query, limit=20)` | FTS5-Volltextsuche, MATCH-Syntax durchgereicht |
| `journal_search_all(...)` | Projektuebergreifende FTS5-Suche mit optionalen Filtern, maximal 100 Treffer |
| `journal_context(project, n_recent=10, include_cross_project=True)` | Kuratierter Session-Startkontext mit offenen Punkten, Bugfixes, Entscheidungen und optionalen Related-Projects |
| `journal_digest(...)` | Strukturierter Zeitraum-Digest mit Counts und kuratierten Entry-Listen |
| `journal_open_items(project=None, limit=50)` | Offene Arbeitspunkte (`status="in_arbeit"`) neueste zuerst, optional pro Projekt gefiltert |
| `journal_list_projects()` | `(project, entry_count, last_timestamp)` pro Projekt, neueste Aktivität zuerst |

Projekt-Identifier müssen im MCP-Layer kebab-case sein
(`^[a-z0-9][a-z0-9-]*$`). `JournalDB` selbst bleibt projekt-agnostisch.

## Setup

```powershell
# Lokales Setup (Windows)
cd C:\Dev\Bramble
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

```bash
# Linux/macOS
cd /opt/bramble
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Server starten

Nach `pip install -e .` ist der Console-Script-Entry-Point
`bramble-server` verfügbar.

### stdio-Transport (für Claude Desktop / Claude Code)

```bash
bramble-server --transport stdio
```

### HTTP-Transport (authentifiziert)

```bash
bramble-server --transport http --host 127.0.0.1 --port 8765 \
    --tokens-file ./secrets/tokens.json --log-level INFO
```

Der HTTP-Transport ist authentifiziert: jeder Tool-Call braucht ein
`Authorization: Bearer <token>`-Header. Tokens werden mit
`scripts/gen_token.py <projekt>` erzeugt. `journal_append` ist an das
Projekt des Tokens gebunden; Lesen und Suchen bleiben
projektübergreifend.

### Konfiguration

Priorität: CLI-Argument > Umgebungsvariable > Default.

| CLI | Env | Default |
|---|---|---|
| `--db PATH` | `BRAMBLE_DB_PATH` | `./data/bramble.db` |
| `--transport stdio\|http` | `BRAMBLE_TRANSPORT` | `stdio` |
| `--host HOST` | `BRAMBLE_HOST` | `127.0.0.1` |
| `--port PORT` | `BRAMBLE_PORT` | `8765` |
| `--log-level LEVEL` | `BRAMBLE_LOG_LEVEL` | `INFO` |
| `--tokens-file PATH` | `BRAMBLE_TOKENS_FILE` | `./secrets/tokens.json` |
| `--rate-limit-per-token N` | `BRAMBLE_RATE_LIMIT_PER_TOKEN` | `60` |
| `--rate-limit-per-ip N` | `BRAMBLE_RATE_LIMIT_PER_IP` | `120` |

Logs werden als JSON auf stderr geschrieben (stdout ist beim stdio-Transport
für das MCP-Protokoll reserviert).

### Admin-UI (Phase 4b)

Der separate Admin-Server rendert Starlette/Jinja2-Views und bindet per
Default nur an `127.0.0.1:8770`. Zugriff ist fuer den Betrieb per
SSH-Tunnel gedacht, nicht ueber einen oeffentlichen Nginx/Plesk-Pfad.

Vor dem Start muss ein Argon2id-Secret existieren. Es gibt bewusst kein
Default-Passwort:

```bash
python scripts/gen_admin_secret.py --output ./secrets/admin-ui.json
```

Danach:

```bash
bramble-admin --db ./data/bramble.db \
    --admin-secret-file ./secrets/admin-ui.json \
    --tokens-file ./secrets/tokens.json
```

Die UI zeigt Dashboard, Projektliste, Projektansicht und Projektsuche.
Zeitstempel bleiben in der DB UTC, werden in der Admin-UI aber mit der
Anzeige-Zeitzone formatiert (`--time-zone`, Env `BRAMBLE_ADMIN_TIME_ZONE`,
Default `Europe/Berlin`) und ohne Sekunden dargestellt.
Zusaetzlich kann sie Projekt-Tokens erzeugen, rotieren und entfernen.
Bestehende Tokenwerte werden nie angezeigt; neue oder rotierte Tokens
erscheinen nur direkt in der Antwort dieser Aktion. Nach Token-
Aenderungen muss `bramble.service` neu gestartet werden, weil der
MCP-Server die Token-Datei beim Start liest.

Schreibende Admin-Aktionen sind CSRF-geschuetzt und werden in der
append-only Tabelle `admin_audit_events` protokolliert. Login-Sessions
bleiben serverseitig, das Cookie ist `HttpOnly` und `SameSite=Strict`;
fehlende oder ungueltige Secrets brechen den Start ab.

### DB ohne Server vorbereiten

```bash
python scripts/init_db.py                # Default ./data/bramble.db
python scripts/init_db.py /tmp/test.db   # explizit
BRAMBLE_DB_PATH=/tmp/test.db python scripts/init_db.py  # per Env
```

`init_db.py` ist idempotent. Beim Server-Start wird ohnehin
`db.initialize()` aufgerufen; das Skript ist nur für Setups, in denen
die DB getrennt vom Server-Lifecycle angelegt werden soll.

## Manuelles End-to-End-Smoke-Testen

`scripts/smoke_http.py` prüft die MCP-Tools gegen einen real
laufenden HTTP-Server (kein Teil der pytest-Suite).

```powershell
# Terminal 1
bramble-server --transport http --host 127.0.0.1 --port 8765 \
    --tokens-file .\secrets\tokens.json --log-level INFO

# Terminal 2
python scripts\smoke_http.py --token <bramble-token>
# oder gegen einen anderen Endpoint:
python scripts\smoke_http.py --url http://127.0.0.1:9000/mcp/ \
    --token <bramble-token>
# read-only Variante ohne Test-Append:
python scripts\smoke_http.py --token <bramble-token> --mode read-only
```

Im Default (`--mode write-light`) schreibt der Smoke-Test zwei Einträge
in die echte DB, liest zurück, sucht per FTS5, prüft
`journal_context`/`journal_digest`/`journal_open_items`, Auth-Gate,
Token-Scope und feuert Negativtests (unbekannter Status, non-kebab
Projektname).

Mit `--mode read-only` werden nur Lese-Checks ausgeführt, inklusive
`journal_context`, `journal_digest`, `journal_open_items` und
`journal_list_projects`, ohne Test-Append in die DB.

## Legacy-Journals importieren

`docs/journal.txt` ist fuer Bramble selbst bereits importiert und wird
nicht weiter gepflegt. Dieser Abschnitt beschreibt nur den Import alter
Textjournals aus Bramble oder anderen Projekten.

`scripts/import_journal_txt.py` importiert bestehende
`docs/journal.txt`-Dateien direkt in die SQLite-DB. Der Default ist ein
Dry-Run; geschrieben wird nur mit `--execute`.

```bash
python scripts/import_journal_txt.py \
    --project bramble \
    --source docs/journal.txt \
    --db data/bramble.db

python scripts/import_journal_txt.py \
    --project bramble \
    --source docs/journal.txt \
    --db data/bramble.db \
    --execute
```

Der Import bewahrt das Journal-Datum, soweit es eindeutig parsebar ist.
Datumseinträge ohne Uhrzeit werden auf `12:00:00+00:00` gesetzt.
Identische Einträge werden im Execute-Modus übersprungen.

## KI-Clients anbinden

MCP-fähige KI-Clients verbinden sich über den öffentlichen HTTP-
Endpunkt und ein projektbezogenes Bearer-Token. Arbeitsregeln,
Korrekturmodell und ein System-Prompt-Baustein stehen in
[docs/ai-client-setup.md](docs/ai-client-setup.md).

Fuer dieses Repo stehen die operativen Agentenregeln zusaetzlich in
[AGENTS.md](AGENTS.md).

## Repo-Struktur

```
Bramble/
├── docs/
│   ├── concepts/        # Phasen-Konzepte
│   ├── ai-client-setup.md
│   └── journal.txt      # Legacy-Importquelle; keine neuen Eintraege
├── deploy/
│   ├── bramble-backup-snapshot.sh
│   ├── bramble-admin.service
│   ├── bramble.service  # systemd-Unit
│   └── fail2ban/        # Fail2Ban-Filter/Jail
├── scripts/
│   ├── gen_admin_secret.py
│   ├── gen_token.py     # Projekt-Token erzeugen/rotieren
│   ├── import_journal_txt.py
│   ├── init_db.py       # Migration / DB-Bootstrap
│   └── smoke_http.py    # manuelles HTTP-Smoke-Skript
├── src/bramble/         # Quellcode (eine Klasse pro Datei)
└── tests/               # pytest-Tests
```

## Lizenz

Proprietär. Keine öffentliche Nutzung vorgesehen.
