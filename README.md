# Bramble

Self-hosted MCP-Server für ein projektübergreifendes Entwicklungs-Journal.

Bramble ist der zentrale "Brombeerstrauch", an dem alle Beeren-Projekte
(Elder-Berry, Bull-Berry, Berry-Gym, Last-Strawberry, ...) hängen.
Statt in jedem Repo eine eigene `docs/journal.txt` zu pflegen, schreiben
alle Projekte ihre Journal-Einträge über das Model-Context-Protocol an
einen gemeinsamen Server, der die Daten in einer SQLite-Datenbank
ablegt und projektübergreifend durchsuchbar macht.

## Status

In aktiver Entwicklung. Aktuell: **Phase 2 – lokal lauffähig** ist
abgeschlossen. Nächstes Ziel: **Phase 3** (Deployment auf
`journal.last-strawberry.com` mit Bearer-Token-Auth, Rate-Limit,
Fail2Ban).

## Phasen-Plan

1. **Phase 1** – Repo-Setup, DB-Schema, Core-Klassen (JournalEntry,
   JournalDB) mit Unit-Tests. ✅
2. **Phase 2** – FastMCP-Server, vier MCP-Tools, CLI-Entry-Point,
   lokal lauffähig. ✅
3. **Phase 3** – Deployment auf `journal.last-strawberry.com`
   (Plesk/Ubuntu), systemd, Nginx-Reverse-Proxy, Bearer-Token-Auth,
   Rate-Limit, Fail2Ban.
4. **Phase 4** – Import bestehender `journal.txt`-Dateien,
   Connector-Setup in Claude.ai und Claude Code.
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

Vier Tools auf dem `JournalMCPServer`, jedes mit `ToolError`-konformer
Fehlerübersetzung:

| Tool | Zweck |
|---|---|
| `journal_read(project, n=80)` | Neueste `n` Einträge für ein Projekt, neueste zuerst |
| `journal_append(project, status, content, phase=None, title=None)` | Neuen Eintrag schreiben; Timestamp wird serverseitig gesetzt |
| `journal_search(project, query, limit=20)` | FTS5-Volltextsuche, MATCH-Syntax durchgereicht |
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

### HTTP-Transport (Vorbereitung für Phase 3, lokal zum Testen)

```bash
bramble-server --transport http --host 127.0.0.1 --port 8765 \
    --log-level INFO
```

### Konfiguration

Priorität: CLI-Argument > Umgebungsvariable > Default.

| CLI | Env | Default |
|---|---|---|
| `--db PATH` | `BRAMBLE_DB_PATH` | `./data/bramble.db` |
| `--transport stdio\|http` | `BRAMBLE_TRANSPORT` | `stdio` |
| `--host HOST` | `BRAMBLE_HOST` | `127.0.0.1` |
| `--port PORT` | `BRAMBLE_PORT` | `8765` |
| `--log-level LEVEL` | `BRAMBLE_LOG_LEVEL` | `INFO` |

Logs werden als JSON auf stderr geschrieben (stdout ist beim stdio-Transport
für das MCP-Protokoll reserviert).

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

`scripts/smoke_http.py` prüft alle vier MCP-Tools gegen einen real
laufenden HTTP-Server (kein Teil der pytest-Suite).

```powershell
# Terminal 1
bramble-server --transport http --host 127.0.0.1 --port 8765 \
    --log-level INFO

# Terminal 2
python scripts\smoke_http.py
# oder gegen einen anderen Endpoint:
python scripts\smoke_http.py --url http://127.0.0.1:9000/mcp/
```

Der Smoke-Test schreibt drei Einträge in die echte DB, liest zurück,
sucht per FTS5, prüft Projekt-Isolation und feuert zwei Negativtests
(unbekannter Status, non-kebab Projektname). Mehrfaches Ausführen sammelt
Einträge an – ggf. `data/bramble.db` löschen für einen sauberen Lauf.

## Repo-Struktur

```
Bramble/
├── docs/
│   ├── concepts/        # Phasen-Konzepte
│   └── journal.txt      # eigenes Journal (bis Phase 4)
├── scripts/
│   ├── init_db.py       # Migration / DB-Bootstrap
│   └── smoke_http.py    # manuelles End-to-End-Smoke-Skript
├── src/bramble/         # Quellcode (eine Klasse pro Datei)
└── tests/               # pytest-Tests
```

## Lizenz

Proprietär. Keine öffentliche Nutzung vorgesehen.
