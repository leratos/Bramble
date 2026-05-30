# Bramble Agent Instructions

Sei ehrlich, beschönige nichts, sei kritisch und weise aktiv auf
Logiklücken, Sicherheitsrisiken, fehlende Tests und technische Schulden hin.
Wenn etwas unklar ist, frage nach statt Annahmen als Fakten zu behandeln.

Diese Datei ist die allgemeine Arbeitsanweisung für LLM-gestützte Coding-
Agents in diesem Repository. Tool-spezifische Dateien wie `CLAUDE.md` sollen
auf diese Datei verweisen, statt ein zweites Regelwerk zu pflegen.

## Arbeitsteilung

- Claude.app: Planung und Konzepterstellung (Phasen-Konzepte, Architektur-
  entscheidungen, Abnahmekriterien in `docs/concepts/`).
- Claude Code (VSCode): Ausführung (Implementierung, Tests, Commits).
- Setze Konzepte um, hinterfrage sie aber kritisch.

## Projektgedächtnis

Projekt:

```text
bramble
```

Bramble nutzt das eigene Bramble-MCP-Journal als aktives Projektgedächtnis.

Die geteilten, projektübergreifenden Journal-Konventionen (Status, Tags,
Korrektur- und `resolves`-Modell, Open-Item-Semantik, Session-Start/Ende,
DoD) sind kanonisch über das MCP-Tool `journal_guide()` abrufbar. Rufe es
zu Beginn jeder Sitzung auf und befolge es; dieses Dokument ergänzt den
Guide nur um Bramble-Repo-Spezifika (Test-Runner, Layout, Deployment) und
wiederholt die geteilten Regeln nicht.

Zu Beginn einer Arbeitssitzung:

- Bevorzuge einen kuratierten Start mit
  `journal_context(project="bramble", n_recent=10)`.
- Fallback für Rohhistorie: `journal_read(project="bramble", n=20)`.
- Wenn der Kontext unklar ist, suche gezielt mit
  - `journal_search(project="bramble", query=..., limit=...)` oder
    projektübergreifend mit
  - `journal_search_all(query=..., limit=...)`.
- Lies zusätzlich relevante lokale Dokumente, wenn sie zum Arbeitsumfang
  gehören:
  - `README.md` für Architektur, Setup, alle MCP-Tools und DB-Schema.
  - `docs/concepts/...` vor Beginn einer Phase oder Änderung am betreffenden
    Konzeptbereich (aktuell `phase-4e-...`).
  - `docs/ai-client-setup.md` für Client-Anbindung und Arbeitsregeln.

Wichtig:

- `docs/journal.txt` ist nur noch eine historische Importquelle. Schreibe dort
  keine neuen Einträge.
- Das Bramble-MCP-Journal ist die maßgebliche Quelle für laufenden Projektstand.
- Bestehende Journal-Einträge werden nicht geändert oder gelöscht.
- Korrekturen sind append-only: schreibe einen neuen `bugfix`- oder `notiz`-
  Eintrag, der den alten Eintrag per id, Titel oder Datum referenziert.
- Wenn die Bramble-MCP-Tools nicht verfügbar sind, sage das ausdrücklich.
  Nutze dann nur als groben Fallback: `git log --oneline -30` und die
  `bramble#<id>`-Referenzen in Commit-Texten. Markiere den Stand als
  grobkörnig und rate nicht.

Während der Arbeit:

- Behandle das Bramble-MCP-Journal als Projektgedächtnis.
- Bei substantiellen Phasen oder längeren Arbeiten: nach Bestätigung einen
  Start-Eintrag mit `journal_append(project="bramble", status="in_arbeit",
  ...)` schreiben.
- Nach abgeschlossener substantieller Arbeit: einen Abschluss-Eintrag mit
  `journal_append(project="bramble", status="abgeschlossen", ...)` schreiben.
- Open-Items schließen (append-only): Sobald Phase 4f deployed ist, den
  Abschluss-Eintrag per Link `resolves -> <id des in_arbeit-Eintrags>`
  verknüpfen. Das ist das zuverlässigste Schließsignal für
  `journal_open_items`/`journal_context`; der alte Eintrag wird nicht
  verändert.
- "Offen" wird inferiert, nicht nur am Status abgelesen:
  `journal_open_items` blendet effektiv geschlossene Items aus und markiert
  alte unresolved-Items als `stale` (`open_state`/`resolution_reason` im
  Output). Echten Backlog (noch nicht begonnene Folgearbeit) als schlanken
  `in_arbeit`-Eintrag mit klarem nächstem Schritt führen — sonst findet ihn
  kein Tool.
- Erlaubte Statuswerte: `in_arbeit`, `abgeschlossen`, `notiz`, `bugfix`.
- Tags (max. 5, lowercase-kebab) aus dem kontrollierten Vokabular: `decision`,
  `deployment`, `security`, `backup`, `import`, `admin-ui`, `test`, `docs`,
  `bug`, `token`, `agent`.
- Erwähne im Journal wichtige Tests, Host-Kommandos, Entscheidungen und offene
  Folgearbeit.

## Planung vor Ausführung

- Nach dem Lesen des Projektgedächtnisses erstelle einen kurzen Plan.
- Warte auf explizite Bestätigung, bevor du mit einer neuen Phase oder
  größeren Code-Änderung beginnst.
- Bei Dateiänderungen: nenne vorher die Dateien, die du ändern wirst.
- Lies bestehende Dateien vor dem Schreiben, auch wenn du ihren Inhalt zu
  kennen glaubst.
- Wenn der Nutzer ausdrücklich direkte Umsetzung verlangt, arbeite trotzdem
  kontrolliert: Kontext lesen, betroffene Dateien nennen, dann umsetzen.

## Code-Generierung

- Neue Code-Dateien: maximal ca. 400 Zeilen pro Datei-Chunk.
- Templates (HTML, Jinja2): nie inline in Python erzeugen, sondern als separate
  Template-Dateien (`src/bramble/templates/admin/...`).
- Verwende relative Pfade vom Projekt-Root, z. B. `src/bramble/...` und
  `tests/...`. Vollqualifizierte Pfade nur, wenn plattformspezifisch nötig
  (lokal `C:\Dev\Bramble\...`, Host `/opt/bramble/...`).
- Halte Änderungen eng am bestehenden Stil und an vorhandenen Abstraktionen.

## Tests

- Runner unter Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

- Ausführen aus dem Projekt-Root.
- Pytest-Konfiguration: `pyproject.toml`, `[tool.pytest.ini_options]`,
  `asyncio_mode = "auto"`.
- Tests liegen flach in `tests/`. Namenskonvention: `test_{modulname}.py`.
- Neue Klasse oder neues Modul: eigener Testfile.
- Nach Code-Änderungen: betroffene Tests ausführen und Ergebnis berichten.
- Mindestens testen: Happy Path, wichtigste Fehlerfälle, relevante Edge Cases.
- Mocks: `unittest.mock` bevorzugen, keine externen Mock-Libraries ohne
  Rückfrage.

## Dependencies

- Source of Truth: `pyproject.toml`. `requirements.txt` nur für Freeze/Export.
- Laufzeit-Deps: `fastmcp` (>=3,<4), `starlette`, `jinja2`, `uvicorn`,
  `argon2-cffi`, `python-json-logger`. Dev: `pytest`, `pytest-asyncio`.
- Keine neuen Dependencies ohne Rückfrage; begründe, warum sie nötig sind.

## Error Handling

- Logging: `logging.getLogger(__name__)`. JSON-Logs auf stderr (stdout ist beim
  stdio-Transport für das MCP-Protokoll reserviert).
- Kein `print()` für Fehler/Warnungen. Kein bare `except:` — fange spezifische
  Exceptions.
- Tool-Fehler im MCP-Layer als `ToolError` übersetzen (bestehendes Muster).
- Fehler mit Format-Argumenten loggen, z. B. `logger.error("Kontext: %s",
  detail)`, nicht mit f-Strings im Log-Call.

## Architekturprinzipien

- OOP: jede Komponente als eigene Klasse, eine Klasse pro Datei (`snake_case`).
- Klassen kommunizieren über definierte Interfaces.
- Dependency Injection: Abhängigkeiten explizit über Konstruktor übergeben.
- Append-only-Datenmodell: keine `update`/`delete`-Tools.
- Hauptklassen: `JournalEntry`, `JournalDB`, `AuthValidator`, `RateLimiter`,
  `JournalMCPServer` (+ Admin-UI). DB-Schema und Tool-Liste: `README.md`.
- Projekt-Identifier im MCP-Layer kebab-case (`^[a-z0-9][a-z0-9-]*$`);
  `JournalDB` bleibt projekt-agnostisch.

## Umgebung

- Dev (Windows): `C:\Dev\Bramble\.venv`, Python 3.12.
- Server (Ubuntu 24.04, Strato): `/opt/bramble/`, eigener systemd-User,
  Python 3.12, hinter Plesk/Nginx auf `journal.last-strawberry.com`.
- Verwende `pathlib` statt hartcodierter Slashes bei plattformübergreifendem
  Code; weise auf plattformspezifischen Code aktiv hin.
- Falls `.venv` fehlt: mit `py -3.12 -m venv .venv` erstellen.

## Sicherheit

- Keine Tokens oder Admin-Secrets ins Journal, in Logs oder ins Repo schreiben.
- Token sind write-gebunden an ihr Projekt; Lesen/Suchen ist projektübergreifend.
- Token-Rotation bleibt auditierbar; nach Token-Änderungen `bramble.service`
  neu starten (der Server liest die Token-Datei beim Start).

## GitHub

- Branch pro Phase: Feature-Arbeit auf `feature/phase-X-kurzbeschreibung`;
  reine Tooling-/Doku-Änderungen auf `chore/kurzbeschreibung`.
- Branch-Namen lowercase, Bindestriche.
- Reihenfolge Journal <-> Commit: zuerst `journal_append` (ID merken), dann
  committen und `bramble#<id>` in den Commit-Text schreiben.
- Am Ende der Phase/des Pakets committen. Vor Commit relevante Tests ausführen
  und im Abschluss nennen.
- Keinen Pull Request erstellen; das macht der Nutzer.

## Sessions und Chat-Management

- Jede neue Phase möglichst eigene Session; sprechende Session-Namen, z. B.
  `phase-4e-journal-workflows`.
- Architekturfragen vor Code-Umsetzung zuerst planen.
- Wenn Antworten unzuverlässiger werden oder Kontext fehlt: aktiv melden.

## Qualität (DoD)

Ein Arbeitspaket gilt erst als sauber abgeschlossen, wenn:

1. Code/Config committed.
2. Relevante Tests/Smoke ausgeführt und genannt.
3. Append-only Journal-Eintrag geschrieben.
4. Nächster Schritt explizit dokumentiert.

- Melde fehlende Tests, Sicherheitslücken, Logiklücken und technische Schulden
  aktiv. Benenne den Preis riskanter Abkürzungen.
- Lieber klein, nachvollziehbar und testbar als große, unklare Umbauten.
