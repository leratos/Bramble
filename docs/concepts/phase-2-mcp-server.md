# Phase 2 – FastMCP-Server (Konzept)

Status: **Konzept, nicht freigegeben.** Vor Phase-2-Beginn die offenen
Designfragen (Abschnitt 2) durchgehen und entscheiden.

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

## 2. Offene Designfragen (vor Phase-Start klären)

### A) FastMCP-Version
FastMCP hatte zwischen 1.x und 2.x Breaking-Changes. Vorschlag:
**aktuelle stabile Version pinnen** (`fastmcp>=2,<3` o.ä.), Pin in
`pyproject.toml` festschreiben. Im neuen Chat zuerst per `pip index`
oder `pypi` den aktuellen Stand prüfen, **bevor** Code geschrieben wird.

### B) Transport: stdio vs HTTP
* **stdio** – Claude Code / Claude Desktop verwenden das nativ. Einfach
  zu testen.
* **HTTP/SSE** – wird in Phase 3 fürs Deployment gebraucht
  (`journal.last-strawberry.com`).

**Vorschlag:** in Phase 2 **beides** vorsehen, mit einem CLI-Flag
(`--transport stdio|http`). Default `stdio` für lokale Entwicklung.
HTTP-Stub schon einrichten, damit Phase 3 nur noch Auth/Proxy ergänzt.

**Alternative:** in Phase 2 nur stdio, HTTP komplett in Phase 3. Spart
Code jetzt, kostet später eine Refactoring-Runde. **Entscheidung steht
aus.**

### C) Sync vs Async DB
FastMCP-Tools sind `async`. Drei Optionen:

1. `JournalDB` bleibt synchron, MCP-Tools rufen sie direkt auf.
   → Blockiert den Event-Loop bei jedem DB-Call. Für ein Tool mit
   <100 req/min und SQLite auf lokaler Platte: vermutlich egal.
2. `JournalDB` bleibt synchron, MCP-Tools wrappen Calls in
   `asyncio.to_thread(...)`.
   → Sauberer, kein Refactoring der Phase-1-Klasse nötig.
3. `JournalDB` wird auf `aiosqlite` umgebaut.
   → Sauberste Variante, aber Phase-1-Tests müssen umgeschrieben werden.

**Vorschlag:** Option 2. Minimaler Eingriff, kein Re-Test der
Phase-1-Klasse. Die `to_thread`-Wraps liegen in `JournalMCPServer`,
nicht in `JournalDB`.

### D) DB-Pfad-Discovery
Woher kennt der Server den Pfad zur SQLite-Datei?

* CLI-Argument (`--db /opt/bramble/data/bramble.db`)
* Umgebungsvariable (`BRAMBLE_DB_PATH`)
* Default (`./data/bramble.db`)

**Vorschlag:** alle drei, in dieser Priorität: CLI > Env > Default.
Phase 3 setzt die Env-Var im systemd-Unit, Phase 2 nutzt den Default.

### E) Projekt-Namen-Validierung im MCP-Layer
`JournalEntry` akzeptiert jeden non-empty String. Sollte der
MCP-Layer zusätzlich kebab-case erzwingen (`^[a-z0-9][a-z0-9-]*$`)?

**Vorschlag:** **Nein, in Phase 2 nicht.** Bramble ist projekt-agnostisch
(siehe System-Prompt). Konvention dokumentieren, nicht erzwingen.
Wenn Phase 5 doch Probleme zeigt, im MCP-Layer nachrüsten – nicht in
`JournalDB`.

### F) `list_projects()`-Rückgabe erweitern?
In Phase 1 als Schuld markiert: liefert nur Namen, kein Count, kein
letzter Timestamp. Für `journal_list_projects` als MCP-Tool wäre das
nützlich („was wurde wo zuletzt geschrieben“).

**Vorschlag:** Erweitern. Neue Methode `JournalDB.project_overview()` →
`list[ProjectSummary]` mit `(name, entry_count, last_timestamp)`.
`list_projects()` bleibt für Legacy/Interne Calls. MCP-Tool nutzt die
neue Methode.

### G) FTS5-Query-Hardening
Phase-1-Schuld: rohe FTS5-Syntax wird durchgereicht. Im MCP-Layer
bewusst gestalten:

* **Option α:** Roh durchreichen, Power-User dürfen `AND`, `NEAR`, etc.
* **Option β:** Standardmäßig in Phrasen-Quotes wrappen (`"foo bar"`),
  Boolean-Operatoren nur über expliziten Parameter.

**Vorschlag:** **Option α** mit Doku im Tool-Description. Bramble wird
von Claude bedient, nicht von Endnutzern – Claude kann FTS5-Syntax
korrekt formulieren.

### H) Fehler-Surfacing in MCP-Tools
`JournalDB` wirft `TypeError` / `ValueError` bei schlechten Inputs.
MCP-Tools sollten diese in MCP-Fehler übersetzen (mit lesbarer Message
für den Client), nicht crashen.

**Vorschlag:** Decorator oder zentraler `try/except`-Block in jedem
Tool, der `ValueError`/`TypeError` in MCP-kompatible Fehler übersetzt.
Andere Exceptions als `RuntimeError` durchreichen + loggen.

### I) Logging
Standard-`logging` mit Modul-Loggern. JSON-Logs oder plain? Für Phase 2
**plain reicht**, JSON kommt in Phase 3 (strukturiertes Logging für
Fail2Ban). Trotzdem schon `logging.basicConfig` zentral, damit Phase 3
nur den Handler tauscht.

### J) Tests
FastMCP bringt typischerweise einen In-Process-Test-Client mit. Damit
`JournalMCPServer` ohne echten Netzwerk-Roundtrip testen.

**Vorschlag:** ein Test pro Tool für Happy-Path + ein Test pro Tool
für den jeweils relevanten Fehler-Pfad. Plus ein Test für
Dependency-Injection (Server akzeptiert übergebene `JournalDB`-Instanz).

---

## 3. Klassen / Dateien (geplant)

| Datei | Klasse | Zweck |
|---|---|---|
| `src/bramble/journal_mcp_server.py` | `JournalMCPServer` | Tool-Registrierung, DI für `JournalDB`, Transport-Wahl |
| `src/bramble/server_config.py` | `ServerConfig` | CLI-Arg + Env + Default zu einem Config-Objekt vereinen |
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
primitive Typen entgegen (kompatibel mit MCP-Schemas).

### `journal_read(project: str, n: int = 80) -> list[dict]`
* **Returns:** Liste von Entry-Dicts (`id`, `project`, `timestamp`,
  `status`, `phase`, `title`, `content`), neueste zuerst.
* **Errors:** `ValueError` bei leerem `project` oder `n<=0`.

### `journal_append(project: str, status: str, content: str, phase: str | None = None, title: str | None = None) -> dict`
* **Returns:** Der neu geschriebene Entry (inkl. zugewiesener `id`).
* **Errors:** `ValueError` bei ungültigem `status` (nicht in
  `JournalStatus`), leerem `project` / `content`.
* **Timestamp** wird **serverseitig** gesetzt (`datetime.now(UTC)`). Der
  Client kann den nicht überschreiben – das schützt vor Uhren-Drift
  und Manipulation.

### `journal_search(project: str, query: str, limit: int = 20) -> list[dict]`
* **Returns:** Trefferliste, neueste zuerst.
* **Errors:** `ValueError` bei leerem `project`/`query`, `limit<=0`.
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
* **Strukturiertes JSON-Logging + Fail2Ban-Hooks:** Phase 3.
* **systemd-Unit, Nginx-Config:** Phase 3.
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
  3. `JournalMCPServer`-Gerüst (Tool-Registrierung leer, DI fertig)
  4. Die vier Tools einzeln, jeder mit eigenem Test-Commit
  5. `__main__.py` + End-to-End-Smoke-Test
* Kein Push, kein PR durch Claude.
