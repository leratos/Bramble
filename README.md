# Bramble

Self-hosted MCP-Server für ein projektübergreifendes Entwicklungs-Journal.

Bramble ist der zentrale "Brombeerstrauch", an dem alle Beeren-Projekte
(Elder-Berry, Bull-Berry, Berry-Gym, Last-Strawberry, ...) hängen.
Statt in jedem Repo eine eigene `docs/journal.txt` zu pflegen, schreiben
alle Projekte ihre Journal-Einträge über das Model-Context-Protocol an
einen gemeinsamen Server, der die Daten in einer SQLite-Datenbank
ablegt und projektübergreifend durchsuchbar macht.

## Status

In aktiver Entwicklung. Aktuell: **Phase 1 – Repo-Setup & Core**.

## Phasen-Plan

1. **Phase 1** – Repo-Setup, DB-Schema, Core-Klassen (JournalEntry, JournalDB)
   mit Unit-Tests.
2. **Phase 2** – FastMCP-Server, MCP-Tools (`journal_read`, `journal_append`,
   `journal_search`, `journal_list_projects`), lokal lauffähig.
3. **Phase 3** – Deployment auf `journal.last-strawberry.com` (Plesk/Ubuntu),
   systemd, Nginx-Reverse-Proxy, Bearer-Token-Auth, Rate-Limit, Fail2Ban.
4. **Phase 4** – Import bestehender `journal.txt`-Dateien, Connector-Setup
   in Claude.ai und Claude Code.
5. **Phase 5** – Migration aller Projekt-System-Prompts auf die MCP-Tools.

## Architektur

- Python 3.12, SQLite (FTS5 für Volltextsuche)
- OOP, eine Klasse pro Datei
- Dependency Injection über den Konstruktor
- Append-only: keine `update`/`delete`-Tools – Korrekturen erfolgen über
  neue Einträge

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

## Entwicklung

```powershell
# Lokales Setup (Windows)
cd C:\Dev\Bramble
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
```

```bash
# DB initialisieren
python scripts/init_db.py
```

## Repo-Struktur

```
Bramble/
├── docs/        # journal.txt (bis DB läuft), Designnotizen
├── scripts/     # init_db.py, Migrationen
├── src/bramble/ # Quellcode (eine Klasse pro Datei)
└── tests/       # pytest-Tests
```

## Lizenz

Proprietär. Keine öffentliche Nutzung vorgesehen.
