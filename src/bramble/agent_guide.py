"""Single source of truth for the shared, project-agnostic agent workflow.

This module is THE canonical copy of the Bramble journal working
conventions that every connected project shares. It is served verbatim by
the ``journal_guide`` MCP tool so that projects do not each maintain (and
drift on) their own copy. A project's ``AGENTS.md`` should reference
``journal_guide()`` and only add project-specific details (project name,
tech stack, test runner, repo layout), not re-state these conventions.

Keep this concise and project-agnostic. When a shared convention changes,
change it here and bump :data:`AGENT_GUIDE_VERSION`; every project picks up
the new version on its next ``journal_guide()`` call.
"""

from __future__ import annotations

# ISO date of the last meaningful change to AGENT_GUIDE. Bump on every edit.
AGENT_GUIDE_VERSION = "2026-05-30"

AGENT_GUIDE = """\
# Bramble Journal – Geteilter Agenten-Arbeitsablauf

Dies ist die kanonische, projektuebergreifende Arbeitsanweisung fuer das
Bramble-MCP-Journal. Sie gilt fuer ALLE Beeren-Projekte. Projekt-AGENTS.md
verweist hierauf und ergaenzt nur Projekt-Spezifisches (Projektname, Stack,
Test-Runner, Repo-Layout) – diese Konventionen werden dort NICHT wiederholt.

## Grundprinzip

Das Journal ist append-only. Eintraege werden nie geaendert oder geloescht.
Korrekturen und Abschluesse sind immer NEUE Eintraege, die den alten per
Link oder Verweis referenzieren.

## Session-Start

1. Immer zuerst: `journal_context(project="<projekt>", n_recent=10)`.
   Leerer Kontext ist kein Fehler – dann bei echter Arbeit einen ersten
   Eintrag schreiben, keinen Smoke-Eintrag.
2. Bei unklarem oder projektuebergreifendem Thema:
   `journal_search_all(query="<thema>", limit=20)`.
3. Faengt ein neuer Arbeitsblock an: einen sauberen `in_arbeit`-Eintrag mit
   klarem Scope und naechstem Schritt schreiben.

## Eintragsarten (Status)

* `in_arbeit`: gestartete Arbeit mit offenem naechstem Schritt.
* `abgeschlossen`: fertig verifizierter Arbeitsblock.
* `notiz`: Entscheidung, Betriebsereignis, Kontext (keine Fehlerkorrektur).
* `bugfix`: Korrektur eines Fehlers, inkl. Korrektur zu altem Eintrag.

Keine neuen Statuswerte erfinden (`todo`/`blocked`/`review`): solche
Information gehoert in Tags oder Inhalt.

## Tags

Kontrolliertes Vokabular, lowercase-kebab, max. 5 pro Eintrag: `decision`,
`deployment`, `security`, `backup`, `import`, `admin-ui`, `test`, `docs`,
`bug`, `token`, `agent`. Tags ergaenzen Status und Phase, ersetzen sie nicht.

## Korrekturen (append-only)

Alten Eintrag nie aendern. Stattdessen neuen `bugfix`-Eintrag schreiben und
per Link `corrects -> <alte id>` (oder per id/Datum im Text) referenzieren.

## Offene Punkte und Abschluss

"Offen" wird inferiert, nicht nur am Status abgelesen. `journal_open_items`
und der `open_items`-Slice von `journal_context` klassifizieren jeden
`in_arbeit`-Eintrag als:

* `resolved` – ein spaeterer Eintrag markiert ihn als erledigt; wird
  standardmaessig ausgeblendet (mit `include_resolved=true` sichtbar samt
  `resolution_reason`/`resolved_by_id`).
* `stale` – unaufgeloest und aelter als `stale_after_days` (Default 30);
  wird angezeigt, aber markiert.
* `open` – unaufgeloest und innerhalb des Fensters.

So schliesst du einen offenen Punkt sauber (in dieser Reihenfolge der
Zuverlaessigkeit):

1. Bevorzugt: Abschluss-Eintrag mit Link `resolves -> <id des
   in_arbeit-Eintrags>`. (Verfuegbar, sobald Bramble Phase 4f deployed ist.)
2. Alternativ explizit: `#<offen> -> #<neu>` im Text des Abschluss-Eintrags.
3. Schwaechere Heuristik (automatisch): ein spaeterer
   `abgeschlossen`/`bugfix`-Eintrag mit gleicher Phase oder gleichem Titel.

Wichtig: Echten Backlog (Folgearbeit, die noch nicht begonnen wurde) als
schlanken `in_arbeit`-Eintrag mit naechstem Schritt fuehren. Was nirgends
als `in_arbeit` steht, kann kein Tool als offen melden.

## Waehrend der Arbeit

* Wichtige Entscheidungen als `notiz` mit Tag `decision`.
* Fehlerkorrekturen als `bugfix`, nie den alten Eintrag aendern.
* Deployment/Backup/Token-Ereignisse als `notiz`/`abgeschlossen` mit
  passenden Tags.

## Session-Ende (Definition of Done)

Ein Arbeitspaket gilt erst als sauber abgeschlossen, wenn:

1. Code/Config committed ist.
2. Relevante Tests/Smoke ausgefuehrt und im Eintrag genannt sind.
3. Ein append-only Journal-Eintrag geschrieben ist.
4. Die offene Folgearbeit explizit als naechster Schritt dokumentiert ist.

## Nicht tun

* Keine `update`/`delete` am Journal; keine Mutation alter Eintraege.
* Keine Tokens/Secrets ins Journal, in Logs oder ins Repo.
* `docs/journal.txt` ist nur historische Importquelle – dort keine neuen
  Eintraege.
"""
