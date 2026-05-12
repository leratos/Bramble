# Phase 2 – FastMCP-Server

Status: **Entschieden, in Umsetzung.** Designfragen aus dem Konzept
sind abgehakt; siehe Abschnitt 2 für die getroffenen Entscheidungen
und Abweichungen vom ursprünglichen Vorschlag.

## 1. Ziel von Phase 2

Bramble wird **lokal lauffähig** als MCP-Server. Vier Tools, alle gegen
die in Phase 1 fertige `JournalDB`. **Kein Deployment**, **keine Auth**,
**kein Rate-Limit** – das ist Phase 3.

End-of-Phase-Kriterium:

* `bramble-server` startet lokal (CLI-Entry-Point).
* Vier MCP-Tools sind erreichbar und über einen Test-Client aufrufbar.
* Tests für `JournalMCPServer` decken Happy-Path und Fehler-Pfade ab.
* `pytest` läuft grün, ohne Netz-Zugriff.

---

## 2. Getroffene Designentscheidungen

### A) FastMCP-Version
**Entschieden:** aktuelle stabile 3.x pinnen (`fastmcp>=3,<4`). Stand
beim Phase-Start war 3.2.4 als neuestes Release der 3er-Linie. Die
2.x-Linie wurde verworfen, weil sie absehbar EOL geht und Phase 3
sonst eine Major-Migration mitschleppen müsste.

### B) Transport: stdio vs HTTP
**Entschieden:** stdio + HTTP-Stub via CLI-Flag. `--transport stdio|http`,
Default `stdio` für lokale Entwicklung. HTTP-Stub schon einrichten,
damit Phase 3 nur noch Auth/Proxy ergänzt.

### C) Sync vs Async DB
**Entschieden:** `JournalDB` bleibt synchron, MCP-Tools wrappen Calls
in `asyncio.to_thread(...)`. Wraps liegen in `JournalMCPServer`, nicht
in `JournalDB`. Kein Re-Test der Phase-1-Klasse nötig.

### D) DB-Pfad-Discovery
**Entschieden:** alle drei Quellen mit Priorität CLI > Env > Default.

* CLI-Argument: `--db /opt/bramble/data/bramble.db`
* Umgebungsvariable: `BRAMBLE_DB_PATH`
* Default: `./data/bramble.db`

Phase 3 setzt die Env-Var im systemd-Unit, Phase 2 nutzt den Default.

### E) Projekt-Namen-Validierung im MCP-Layer
**Entschieden (Abweichung vom Konzept-Vorschlag):** kebab-case
**wird im MCP-Layer erzwungen** (`^[a-z0-9][a-z0-9-]*$`). `JournalDB`
bleibt projekt-agnostisch und akzeptiert weiter jeden non-empty String –
die Härtung lebt nur im MCP-Tool. So bleibt die Phase-1-Klasse für
interne/legacy Calls offen, während das öffentliche Tool eine
einheitliche Konvention durchsetzt.

### F) `list_projects()`-Rückgabe erweitern
**Entschieden:** neue Methode `JournalDB.project_overview()` →
`list[ProjectSummary]` mit `(name, entry_count, last_timestamp)`.
`list_projects()` bleibt für Legacy/Interne Calls. MCP-Tool nutzt die
neue Methode.

### G) FTS5-Query-Hardening
**Entschieden:** roh durchreichen, mit klarer Doku im Tool-Description.
Bramble wird von Claude bedient, nicht von Endnutzern – Claude kann
FTS5-Syntax (AND, NEAR, Phrasen-Quotes) korrekt formulieren. Fehlerhafte
Syntax liefert weiterhin eine leere Liste (Phase-1-Verhalten).

### H) Fehler-Surfacing in MCP-Tools
**Entschieden:** Decorator pro Tool. Ein zentraler `@translate_errors`-
Decorator wandelt `ValueError` / `TypeError` aus `JournalDB` in
MCP-kompatible Fehler mit lesbarer Message für den Client. Andere
Exceptions werden als `RuntimeError` durchgereicht und zusätzlich
geloggt.

### I) Logging
**Entschieden (Abweichung vom Konzept-Vorschlag):** **JSON-Logging
schon in Phase 2**, via `python-json-logger`. Modul-Logger pro Datei,
zentrales Setup in einem `logging_setup`-Modul. So kann Phase 3 direkt
Fail2Ban anschliessen, ohne nochmals am Format zu drehen.

### J) Tests
**Entschieden:** FastMCP-In-Process-Test-Client. Pro Tool ein
Happy-Path-Test und ein Test für den jeweils relevanten Fehler-Pfad.
Plus ein DI-Test (Server akzeptiert eine übergebene `JournalDB`-
Instanz).

---

## 3. Klassen / Dateien (geplant)

| Datei | Klasse | Zweck |
|---|---|---|
| `src/bramble/journal_mcp_server.py` | `JournalMCPServer` | Tool-Registrierung, DI für `JournalDB`, Transport-Wahl |
| `src/bramble/server_config.py` | `ServerConfig` | CLI-Arg + Env + Default zu einem Config-Objekt vereinen |
| `src/bramble/logging_setup.py` | – | Zentrales JSON-Logging via `python-json-logger` |
| `src/bramble/mcp_errors.py` | – | `@translate_errors`-Decorator für Tool-Funktionen |
| `src/bramble/__main__.py` | – | CLI-Entry-Point (`python -m bramble` oder Console-Script `bramble-server`) |
| `src/bramble/journal_db.py` | + `project_overview()` | Phase-1-Klasse, einzige Erweiterung: neue Read-Methode |

**Konsequent „eine Klasse pro Datei":** `ServerConfig` bekommt eine
eigene Datei, nicht reingequetscht in den Server.

Optional, falls Phase 2 in einem Chat zu viel wird:
* `journal_mcp_tools.py` – Tool-Funktionen separat, damit der Server
  selbst klein bleibt. **Vorschlag:** erst aufteilen, wenn das
  Server-File >300 Zeilen wird.

---

## 4. Tool-Verträge

Alle Tools sind `async` und nehmen ausschließlich validierbare,
primitive Typen entgegen (kompatibel mit MCP-Schemas). `project` muss
in **allen** Tools dem Muster `^[a-z0-9][a-z0-9-]*$` entsprechen
(Entscheidung E).

### `journal_read(project: str, n: int = 80) -> list[dict]`
* **Returns:** Liste von Entry-Dicts (`id`, `project`, `timestamp`,
  `status`, `phase`, `title`, `content`), neueste zuerst.
* **Errors:** `ValueError` bei leerem / nicht-kebab-case `project` oder
  `n<=0`.

### `journal_append(project: str, status: str, content: str, phase: str | None = None, title: str | None = None) -> dict`
* **Returns:** Der neu geschriebene Entry (inkl. zugewiesener `id`).
* **Errors:** `ValueError` bei ungültigem `status` (nicht in
  `JournalStatus`), leerem / nicht-kebab-case `project`, leerem
  `content`.
* **Timestamp** wird **serverseitig** gesetzt (`datetime.now(UTC)`). Der
  Client kann den nicht überschreiben – das schützt vor Uhren-Drift
  und Manipulation.

### `journal_search(project: str, query: str, limit: int = 20) -> list[dict]`
* **Returns:** Trefferliste, neueste zuerst.
* **Errors:** `ValueError` bei leerem / nicht-kebab-case `project`,
  leerem `query`, `limit<=0`.
* Fehlerhafte FTS5-Syntax liefert leere Liste (wie in Phase 1).

### `journal_list_projects() -> list[dict]`
* **Returns:** `[{"project": ..., "entry_count": N, "last_timestamp": "..."}]`,
  sortiert nach `last_timestamp DESC`.
* **Errors:** keine erwarteten.

---

## 5. Was bewusst NICHT in Phase 2 gehört

* **Auth (Bearer-Token):** Phase 3. In Phase 2 läuft der Server ungeschützt,
  weil nur lokal über stdio bzw. `127.0.0.1` erreichbar.
* **Rate-Limit:** Phase 3.
* **Fail2Ban-Hooks / systemd-Unit / Nginx-Config:** Phase 3. Das
  Log-Format ist bereits JSON, sodass Phase 3 nur noch den Filter
  schreiben muss.
* **`AuthValidator` / `RateLimiter`-Klassen:** Phase 3. *Aber*: die DI
  in `JournalMCPServer` muss so gebaut sein, dass die in Phase 3
  einfach mit reingereicht werden – Konstruktor-Slots planen, nicht
  monkey-patchen.

---

## 6. Risiken & Schulden, die ich von Anfang an im Auge habe

* **FastMCP-API-Stabilität.** Die API hat sich in den letzten 12 Monaten
  bewegt. Beim ersten Versuch ein triviales Demo-Tool registrieren, um
  zu verifizieren, dass die Doku zur installierten Version passt –
  **bevor** die vier echten Tools gebaut werden.
* **stdio-Tests können hängen.** Wenn der FastMCP-Test-Client falsch
  gehandhabt wird, blockieren Tests den pytest-Lauf. Timeouts setzen.
* **`asyncio.to_thread` und SQLite:** SQLite-Connection-Objekte sind
  per Default an den erzeugenden Thread gebunden. Da `JournalDB`
  pro Methode eine neue Connection öffnet, ist das kein Problem –
  aber: explizit testen.
* **DB-Pfad-Defaults auf Windows vs Linux:** Phase 2 testet auf Windows,
  Phase 3 deployt auf Linux. `pathlib` konsequent verwenden, keine
  hartcodierten Slashes.

---

## 7. Branch & Commit-Strategie

* Branch: `feature/phase-2-mcp-server`
* Commits in Etappen, nicht ein einziger Mega-Commit:
  1. `project_overview()` + Tests (kleine Erweiterung Phase-1-Code)
  2. `ServerConfig`-Klasse + Tests
  3. `logging_setup` + `mcp_errors`-Decorator + Tests
  4. `JournalMCPServer`-Gerüst (Tool-Registrierung leer, DI fertig)
  5. Die vier Tools einzeln, jeder mit eigenem Test-Commit
  6. `__main__.py` + End-to-End-Smoke-Test
* Kein Push, kein PR durch Claude.
