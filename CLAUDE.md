# CLAUDE.md — Arbeitsanweisung Claude Code (Bramble)

## Rolle & Arbeitsteilung
- **Claude Code (VSCode) = Ausführung:** implementieren, testen, committen.
- **Claude.app = Planung/Konzept:** Phasen-Konzepte, Architekturentscheidungen
  und Abnahmekriterien entstehen dort (siehe `docs/concepts/`). Setze sie hier
  um — aber hinterfrage sie.
- Sei ehrlich, beschönige nichts, sei kritisch. Weise aktiv auf Logiklücken,
  Fehler, fehlende Tests, Sicherheitslücken und technische Schulden hin.
  Bei Unklarheit: nachfragen statt annehmen.

## Journal (Projektgedächtnis)
Verbindliche Regeln: @AGENTS.md. Kurzfassung:
- Session-Start: `journal_context(project="bramble", n_recent=10)`
  (Fallback `journal_read`).
- Ende substanzieller Arbeit: `journal_append(project="bramble", ...)`.
- Append-only: alte Einträge nie ändern; Korrektur = neuer `bugfix`/`notiz`,
  der den alten per id/Titel/Datum referenziert.
- **Niemals** in `docs/journal.txt` schreiben (nur historische Importquelle).
- Status: `in_arbeit | abgeschlossen | notiz | bugfix`. Tags (max. 5,
  lowercase-kebab) aus: decision, deployment, security, backup, import,
  admin-ui, test, docs, bug, token, agent.

## Plan vor Ausführung
- Nach dem Journal-Lesen: kurzen Plan nennen — was du tust, welche Dateien
  du änderst.
- **Auf explizite Bestätigung warten, bevor du Code schreibst oder Dateien
  änderst.** Nie ungefragt drauflos.

## Architektur (verbindlich)
- OOP: eine Klasse pro Datei, Dateiname = Klassenname (snake_case).
- Dependency Injection über den Konstruktor; definierte Interfaces.
- Neue Komponente = neue Klasse, nicht als Funktion in bestehende Datei kippen.
- Append-only-Datenmodell: kein update/delete.
- `pathlib` statt hartcodierter Slashes; plattformspezifischen Code kennzeichnen.

## Umgebung
- Dev (Windows): `C:\Dev\Bramble\.venv`, Python 3.12.
  Server (Ubuntu 24.04): `/opt/bramble/`, eigener systemd-User, Python 3.12.
- Absolute Pfade verwenden. Fehlt `.venv` lokal: `py -3.12 -m venv .venv`.
- Nach Code-Änderungen betroffene Tests ausführen und Ergebnis berichten:
  `.\.venv\Scripts\python -m pytest ...`.

## Code-Generierung
- Bestehende Dateien VOR dem Schreiben lesen — immer.
- Templates/Configs nie inline, immer als eigene Datei.

## Git
- Repo `github.com/leratos/Bramble`. Branch-Namen lowercase, Bindestriche.
- **Branch pro Phase:** Feature-Arbeit auf `feature/phase-X-kurzbeschreibung`;
  reine Tooling-/Doku-Änderungen auf `chore/kurzbeschreibung`. Am Ende der
  Phase/des Pakets alle Änderungen committen.
- **Reihenfolge Journal ↔ Commit:** zuerst `journal_append` (ID merken),
  dann committen und `bramble#<id>` in den Commit-Text schreiben — so sind
  Journal-Eintrag und Commit beidseitig auffindbar.
- Keine Pull-Requests — macht der Nutzer.

## Qualität (DoD)
„Sauber abgeschlossen" erst wenn: (1) Code/Config committed, (2) relevante
Tests/Smoke ausgeführt und genannt, (3) append-only Journal-Eintrag
geschrieben, (4) nächster Schritt explizit dokumentiert.
- pytest `asyncio_mode=auto`; `logging.getLogger`; keine bare `except`;
  Tests Pflicht für Core-Klassen.

## Referenzen
- `README.md` — Architektur, Setup, Server-Start, alle 8 MCP-Tools, DB-Schema.
- `AGENTS.md` — verbindliche Journal-Regeln.
- `docs/ai-client-setup.md` — Client-Anbindung & Arbeitsregeln.
- `docs/concepts/` — Phasen-Konzepte (aktuell `phase-4e-...`).
