# Phase 4 – Import & Connector-Setup

Status: **In Arbeit** (2026-05-26). Backup/Restore ist verifiziert;
der Importer für legacy `journal.txt`-Dateien ist als erstes
Phase-4-Arbeitspaket umgesetzt.

Phase 4 macht Bramble selbst zum produktiv genutzten Journal: bestehende
`journal.txt`-Dateien werden importiert, anschließend schreiben Claude.ai
und Claude Code über die MCP-Tools direkt in die zentrale Datenbank.

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

1. Brambles eigenes [docs/journal.txt](../journal.txt) importieren.
2. Die zwei Smoke-Test-Einträge in der Produktiv-DB prüfen und entweder
   bewusst behalten oder vor dem Import manuell entfernen.
3. Elder-Berry importieren.
4. Weitere Projekte projektweise ergänzen: Bull-Berry, Berry-Gym,
   Last-Strawberry und spätere Repos.

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

Claude.ai und Claude Code bekommen jeweils den HTTP-Endpunkt:

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

## 6. Offene Entscheidungen

* Umgang mit sehr alten oder handformatierten Journal-Abschnitten, die
  kein klares `Datum:` haben.
* Ob Smoke-Test-Einträge vor Brambles Eigenimport gelöscht werden oder
  als Betriebsnachweis erhalten bleiben.
