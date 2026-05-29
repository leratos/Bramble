# Bramble AI Client Setup

Status: Arbeitsanleitung für Codex, Claude und andere MCP-fähige KI-
Clients.

## Ziel

KI-Clients sollen Bramble als gemeinsames Entwicklungsjournal nutzen:

* alte Einträge lesen,
* projektbezogen oder projektuebergreifend suchen,
* neue Einträge schreiben,
* Korrekturen nachvollziehbar ergänzen.

Bramble bleibt bewusst **append-only**. Es gibt kein Update/Delete-Tool.
Wenn ein Eintrag korrigiert werden muss, wird ein neuer `bugfix`- oder
`notiz`-Eintrag angelegt, der den alten Eintrag per `id`, Titel oder
Datum referenziert.

Fuer Brambles eigenes Repo sind die verbindlichen Agentenregeln in
[`AGENTS.md`](../AGENTS.md) abgelegt. `docs/journal.txt` ist nur noch
eine historische Importquelle; neue Eintraege werden ausschliesslich
ueber das MCP-Journal geschrieben.

## Verbindung

HTTP-MCP-Endpunkt:

```text
https://journal.last-strawberry.com/mcp/
```

Jeder HTTP-Request braucht den Header:

```text
Authorization: Bearer <project-token>
```

Für Bramble selbst ist das Projekt:

```text
bramble
```

Das Token liegt nur auf dem Host in:

```text
/opt/bramble/secrets/tokens.json
```

Tokens werden nicht ins Repo geschrieben und nicht in Chatprotokolle
kopiert.

## Erwartete Tools

Der Client sollte nach erfolgreicher Verbindung diese Tools sehen:

| Tool | Nutzung |
| --- | --- |
| `journal_read(project, n=80)` | Letzte Einträge eines Projekts lesen |
| `journal_append(project, status, content, phase=None, title=None)` | Neuen Eintrag schreiben |
| `journal_search(project, query, limit=20)` | Volltextsuche in einem Projekt |
| `journal_search_all(...)` | Volltextsuche ueber alle Projekte mit optionalen Filtern |
| `journal_context(project, n_recent=10, include_cross_project=True)` | Kuratierter Session-Startkontext fuer ein Projekt |
| `journal_digest(...)` | Zeitraum-Digest mit Counts, offenen Punkten, Bugfixes und Entscheidungen |
| `journal_open_items(project=None, limit=50)` | Offene Arbeitspunkte neueste zuerst, optional pro Projekt gefiltert |
| `journal_list_projects()` | Projekte mit Counts und letzter Aktivität listen |

`journal_append` ist an das Projekt des Tokens gebunden. Ein
`bramble`-Token darf also nur in `project="bramble"` schreiben. Lesen
und Suchen bleiben projektübergreifend.

## Arbeitsregeln für KI-Agenten

Zu Beginn einer Bramble-Session:

1. Bevorzugt `journal_context(project="bramble", n_recent=10)` aufrufen.
  Fallback: `journal_read(project="bramble", n=20)`.
2. Bei unklarer Historie gezielt suchen, z. B.
   `journal_search(project="bramble", query="Phase 4", limit=10)`.
   Wenn das relevante Projekt unklar ist, `journal_search_all(...)` nutzen.
3. Falls ein neuer Arbeitsblock startet: frueh einen klaren
  `in_arbeit`-Eintrag mit Scope und naechstem Schritt anlegen.
4. Die gelesenen Einträge bei Planung und Statusantworten berücksichtigen.

Während der Arbeit:

* Relevante Entscheidungen, abgeschlossene Arbeitspakete, Bugs und
  Betriebsereignisse als neuen Eintrag dokumentieren.
* In Brambles eigenem Repo keine neuen Eintraege in `docs/journal.txt`
  schreiben.
* Keine triviale Zwischenmeldung journalisieren; Bramble ist ein
  Entwicklungsjournal, kein Token-by-Token-Log.
* Den `phase`-Wert setzen, wenn er natürlich passt, z. B. `Phase 4`.
* Kurze, konkrete `title`-Werte verwenden.

Am Ende einer substanziellen Arbeit:

* Einen Abschluss- oder Fortschrittseintrag schreiben.
* Tests, Host-Kommandos und offene nächste Schritte im `content`
  erwähnen.
* Vor Abschluss diese DoD-Checks einhalten:
  1. Code/Config committed.
  2. Relevante Tests oder Smoke-Checks gelaufen.
  3. Append-only Journal-Eintrag geschrieben.
  4. Naechster Schritt explizit dokumentiert.

## Status-Werte

| Status | Bedeutung |
| --- | --- |
| `in_arbeit` | Arbeit begonnen, noch offen |
| `abgeschlossen` | Arbeitspaket abgeschlossen |
| `notiz` | Betriebsnotiz, Entscheidung, Kontext |
| `bugfix` | Fehler behoben oder Korrektur zu einem alten Eintrag |

## Korrekturen und Anpassungen

Bestehende Einträge werden nicht überschrieben. Stattdessen:

```text
status: bugfix
title: Korrektur zu Eintrag <id oder Titel>
content:
Korrigiert den Eintrag "<alter Titel>" vom <Datum>.

Alt:
<kurze Beschreibung der falschen Aussage>

Neu:
<korrigierte Aussage>

Auswirkung:
<falls relevant: was daraus folgt>
```

Für kleinere Ergänzungen ohne Fehler:

```text
status: notiz
title: Nachtrag zu <Thema>
content:
Ergänzt den Eintrag "<alter Titel>" um ...
```

## Beispiel: Eintrag Schreiben

```json
{
  "project": "bramble",
  "status": "abgeschlossen",
  "phase": "Phase 4",
  "title": "AI-Client-Setup dokumentiert",
  "content": "Bramble wurde fuer MCP-faehige KI-Clients dokumentiert. Agenten lesen zu Beginn journal_read(...), schreiben Fortschritt per journal_append(...) und korrigieren append-only ueber bugfix/notiz-Nachtraege."
}
```

## Minimaler System-Prompt-Baustein

```text
Nutze Bramble als projektbezogenes Entwicklungsjournal.

Projekt: bramble

Zu Beginn relevanter Arbeit rufe bevorzugt
journal_context(project="bramble", n_recent=10) auf.
Alternativ journal_read(project="bramble", n=20).
Suche bei Bedarf mit journal_search(project="bramble", query=...)
oder projektuebergreifend mit journal_search_all(query=...).
Schreibe am Ende substanzieller Arbeit einen journal_append-Eintrag.
Bestehende Einträge werden nicht geändert; Korrekturen erfolgen als neue
bugfix- oder notiz-Einträge, die den alten Eintrag referenzieren.
Nutze nur Statuswerte: in_arbeit, abgeschlossen, notiz, bugfix.
```

## Verifikation eines neuen Clients

1. Tool-Liste prüfen: alle acht Bramble-Tools müssen sichtbar sein.
1. Lesen testen:

```text
journal_read(project="bramble", n=5)
```

1. Suche testen:

```text
journal_search(project="bramble", query="Backup", limit=5)
```

1. Projektuebergreifende Suche testen:

```text
journal_search_all(query="Backup", limit=5)
```

1. Digest testen:

```text
journal_digest(project="bramble", since="7d")
```

1. Session-Kontext testen:

```text
journal_context(project="bramble", n_recent=10)
```

1. Open-Items testen:

```text
journal_open_items(project="bramble", limit=10)
```

1. Schreibtest nur als echten Journal-Eintrag ausführen, nicht als
  beliebigen Smoke-Eintrag. Beispiel: `title="Client <name> angebunden"`.

Wenn ein Schreibtest fehlschlägt, zuerst prüfen:

* Ist der Authorization-Header gesetzt?
* Gehört das Token zum Projekt `bramble`?
* Schreibt der Client wirklich nach `project="bramble"`?
* Ist der Client eventuell durch Fail2Ban gesperrt?
