# Phase 4 – Import & Connector-Setup

Status: **In Arbeit** (2026-05-26). Backup/Restore ist verifiziert;
der Importer für legacy `journal.txt`-Dateien ist umgesetzt; Brambles
eigenes Journal ist in die Produktiv-DB importiert. Codex ist als erster
Bramble-MCP-Client angebunden.

Phase 4 macht Bramble selbst zum produktiv genutzten Journal:
Zuerst werden MCP-fähige KI-Clients für das Projekt `bramble`
angebunden, dann werden bestehende `journal.txt`-Dateien importiert,
anschließend schreiben Claude.ai, Claude Code, Codex und andere Clients
über die MCP-Tools direkt in die zentrale Datenbank.

## 1. Voraussetzung

Schritt 8 aus dem Deployment-Runbook ist abgeschlossen und am
2026-05-26 gegen das Archiv `server-2026-05-26_20:39` verifiziert:

* Borg sichert `/opt/bramble/backup-staging/bramble.db`.
* Borg sichert `/opt/bramble/secrets/tokens.json`.
* Der Restore-Test liefert `PRAGMA integrity_check;` → `ok`.
* `SELECT COUNT(*) FROM journal_entries;` → `2`.
* `SELECT COUNT(*) FROM journal_fts;` → `2`.

Damit ist der Backup-Stopper vor Phase 4 erledigt. Der Import ist zwar
append-only, aber er verändert die zentrale Produktiv-DB dauerhaft; pro
Projekt wird deshalb weiterhin erst ein Dry-Run gemacht.

## 2. Import-Reihenfolge

1. Brambles eigenes [docs/journal.txt](../journal.txt) importieren. ✅
2. Die zwei Smoke-Test-Einträge in der Produktiv-DB prüfen und vor dem
   Import entfernen. ✅
3. Bramble als Journal-Tool für KI-Clients konfigurieren. ✅
4. Elder-Berry importieren.
   Lokaler Dry-Run gegen `C:\Dev\Elder-Berry\docs\journal.txt` ist
   parsebar: `174 entries`, `0 issues`.
5. Berry-Gym importieren.
   Lokaler Dry-Run gegen `C:\Dev\Berry-Gym\docs\journal.txt` ist
   parsebar: `303 entries`, `0 issues`.
6. Weitere Projekte projektweise ergänzen: Bull-Berry, Last-Strawberry
   und spätere Repos, falls dort noch legacy `journal.txt`-Quellen
   existieren.

Für jedes Projekt wird vorher ein eigenes Token erzeugt:

```sh
sudo -u bramble BRAMBLE_TOKENS_FILE=/opt/bramble/secrets/tokens.json \
    /opt/bramble/.venv/bin/python /opt/bramble/scripts/gen_token.py elder-berry
systemctl restart bramble
```

## 3. Import-Strategie

Der Import sollte als eigenes Script entstehen, nicht per Hand über
`journal_append`. Script: `scripts/import_journal_txt.py`.

Eigenschaften:

* Default ist Dry-Run: erkannte Einträge, Status, Titel und Datum werden
  angezeigt, aber nichts wird geschrieben.
* `--project <name>` erzwingt das Zielprojekt und nutzt dieselbe
  kebab-case-Regel wie der MCP-Layer.
* `--source <path>` liest eine bestehende `journal.txt`.
* `--db <path>` schreibt direkt über `JournalDB`, nicht über HTTP, damit
  der Import auf dem Host ohne Rate-Limit und ohne Connector-Tokens
  laufen kann.
* Importierte Einträge behalten, soweit sauber ableitbar, ihr
  Journal-Datum. Wenn nur ein Datum ohne Uhrzeit vorhanden ist, wird
  `12:00:00+00:00` verwendet und der Eintrag bekommt im Titel oder Inhalt
  keinen künstlichen Zusatz.
* Nicht eindeutig parsebare Abschnitte werden im Dry-Run gemeldet und
  erst nach manueller Entscheidung importiert.
* Identische Einträge werden im Execute-Modus standardmäßig
  übersprungen, damit ein versehentlicher zweiter Import keine
  Duplikate erzeugt. `--allow-duplicates` ist nur für bewusste
  Sonderfälle gedacht.
* Elder-Berry- und Berry-Gym-Legacy-Formate werden unterstuetzt:
  * Datum im Heading, z. B. `(2026-05-10)`.
  * Deutsche Datumsschreibweise im Heading, z. B. `(20.02.2026)`.
  * Markdown-Datumszeilen wie `**Datum:** YYYY-MM-DD`.
  * `- Datum: YYYY-MM-DD` im Body.
  * zusaetzliche Statuslabels wie `Hotfix`, `Korrektur`, `Nachtrag`,
    `Stand`, `Update`, `Konzept`, `Abschluss`.
  * Berry-Gym-Statuslabels wie `Fix`, `Final`, `Test`, `Geplant`,
    `Roadmap-Fix`, `Roadmap-Update`, `Teilweise Abgeschlossen` und
    `Vollstaendig Abgeschlossen`.
  * Unpraefixierte Berry-Gym-Headings wie `TODO (offen)`, `PUBLIC
    LAUNCH`, Roadmap-/Konzeptnotizen, implementierte Phasen und
    empirische Klaerungen werden konservativ auf Bramble-Status
    abgebildet.
  * Metadaten-Headings `## Branch:` und `## Naechster Schritt:` werden
    als Body-Metadaten des aktuellen Abschnitts behandelt.
  * CLI-Ausgaben ersetzen nicht darstellbare Zeichen, statt unter
    Windows-Codepages am Dry-Run-Report abzubrechen.

Dry-Run für Brambles eigenes Journal auf dem Host:

```sh
sudo -u bramble /opt/bramble/.venv/bin/python \
    /opt/bramble/scripts/import_journal_txt.py \
    --project bramble \
    --source /opt/bramble/docs/journal.txt \
    --db /opt/bramble/data/bramble.db
```

Wenn der Dry-Run `issues: 0` meldet, Import ausführen:

```sh
sudo -u bramble /opt/bramble/.venv/bin/python \
    /opt/bramble/scripts/import_journal_txt.py \
    --project bramble \
    --source /opt/bramble/docs/journal.txt \
    --db /opt/bramble/data/bramble.db \
    --execute
```

## 4. Mindest-Verifikation pro Projekt

Nach jedem Projektimport:

```sh
sqlite3 /opt/bramble/data/bramble.db "PRAGMA integrity_check;"
python /opt/bramble/scripts/smoke_http.py \
    --url https://journal.last-strawberry.com/mcp/ \
    --token <project-token> \
    --project <project>
```

Außerdem über MCP prüfen:

* `journal_read(project, n=5)` zeigt die neuesten importierten Einträge.
* `journal_search(project, "ein eindeutiges Stichwort", limit=5)` findet
  erwartete Alt-Einträge.
* `journal_list_projects()` enthält das Projekt mit plausibler
  `entry_count`.

## 5. Connector-Setup

Allgemeine Anleitung, Arbeitsregeln und System-Prompt-Baustein:
[docs/ai-client-setup.md](../ai-client-setup.md).

Claude.ai, Claude Code, Codex und andere MCP-fähige Clients bekommen
jeweils den HTTP-Endpunkt:

```text
https://journal.last-strawberry.com/mcp/
```

Für jedes Projekt wird das zugehörige Bearer-Token als Authorization-
Header eingetragen:

```text
Authorization: Bearer <project-token>
```

Wichtig für System-Prompts ab Phase 5:

* Neue Journal-Einträge immer mit `journal_append` schreiben.
* `project` muss das eigene Projekt sein.
* Vor größerer Arbeit `journal_read(project, n=20)` und bei Bedarf
  `journal_search(project, query, limit=10)` nutzen.
* Korrekturen werden als neuer `bugfix`- oder `notiz`-Eintrag
  geschrieben; es gibt bewusst kein Update/Delete-Tool.

Für Bramble selbst gilt schon in Phase 4:

* Projekt: `bramble`.
* Zu Beginn relevanter Arbeit `journal_read(project="bramble", n=20)`.
* Bei Bedarf Suche per `journal_search(project="bramble", query=...)`.
* Fortschritt, Entscheidungen und Abschlüsse per `journal_append`.
* Anpassungen/Korrekturen immer append-only als neuer `bugfix`- oder
  `notiz`-Eintrag mit Referenz auf den alten Eintrag.
* Operative Repo-Regeln stehen in [AGENTS.md](../../AGENTS.md).
* `docs/journal.txt` bleibt nur historische Importquelle und wird nicht
  mehr fuer neue Eintraege verwendet.

## 6. Offene Entscheidungen

* Umgang mit sehr alten oder handformatierten Journal-Abschnitten, die
  kein klares `Datum:` haben.
