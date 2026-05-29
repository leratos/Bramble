# Phase 4d - Journal-Kontexttools

Status: Konzept (2026-05-29).

Dieses Dokument beschreibt neue Lese- und Kontextfunktionen fuer Bramble.
Ziel ist, dass KI-Clients und Menschen schneller den relevanten
Projektstand verstehen, ohne grosse Mengen roher Journal-Eintraege lesen
zu muessen.

## 1. Ziel

Die bestehenden Tools sind bewusst klein:

* `journal_read(project, n)`
* `journal_search(project, query, limit)`
* `journal_append(...)`
* `journal_list_projects()`

Das reicht fuer den Betrieb. Fuer hohe Alltagstauglichkeit fehlen aber:

* ein kuratierter Startkontext fuer KI-Sessions,
* projektuebergreifende Suche,
* Digests fuer Zeitraeume,
* offene Arbeitspunkte und letzte Entscheidungen auf einen Blick.

## 2. Leitprinzipien

* Read-only Kontexttools duerfen projektuebergreifend lesen, wie die
  bestehende Entscheidung fuer `journal_read` und `journal_search`.
* Schreibrechte bleiben unveraendert tokengebunden.
* Kontexttools sollen deterministisch und nachvollziehbar sein.
* Keine LLM-Zusammenfassung im Server-MVP. Der Server liefert
  strukturierte Rohdaten und kurze regelbasierte Verdichtungen.
* Spaetere LLM-Zusammenfassungen muessen als eigene Entscheidung
  behandelt werden.

## 3. Tool: `journal_search_all`

### Empfehlung

Ein neues MCP-Tool fuer projektuebergreifende Suche einfuehren:

```text
journal_search_all(query, limit=20, projects=None, statuses=None, tags=None)
```

### Nutzen

Brambles Kernidee ist projektuebergreifendes Gedaechtnis. Aktuell muss
ein Client wissen, in welchem Projekt gesucht werden soll. Das ist bei
Themen wie Backup, Deployment, Matrix, Admin-UI oder Git-Hooks oft
kuenstlich.

### Rueckgabe

Jeder Treffer sollte mindestens enthalten:

* `id`
* `project`
* `timestamp`
* `status`
* `phase`
* `title`
* `content`
* optional spaeter `tags`, `actor`, `client`

### Sicherheitsregel

Wie bei `journal_read` gilt: Lesen ist projektuebergreifend erlaubt.
Das Tool darf keine Schreibwirkung haben.

## 4. Tool: `journal_context`

### Empfehlung

Ein MCP-Tool fuer den Start einer Arbeitssitzung:

```text
journal_context(project, n_recent=10, include_cross_project=True)
```

### Ziel

Ein KI-Client soll am Sitzungsstart nicht nur die letzten 20 Eintraege
roh lesen, sondern einen sortierten Arbeitskontext bekommen.

### Rueckgabestruktur

```json
{
  "project": "elder-berry",
  "recent": [],
  "open_items": [],
  "recent_bugfixes": [],
  "recent_decisions": [],
  "related_projects": [],
  "suggested_searches": []
}
```

### Auswahlregeln fuer MVP

`recent`:

* neueste `n_recent` Eintraege des Projekts.

`open_items`:

* neueste Eintraege mit `status="in_arbeit"`.
* spaeter: Eintraege, die nicht durch `supersedes` oder
  `adds_context_to` geschlossen wurden.

`recent_bugfixes`:

* letzte Eintraege mit `status="bugfix"`.

`recent_decisions`:

* Eintraege mit Tag `decision`, falls Tags existieren.
* Bis dahin: Titel oder Inhalt enthaelt "Entscheidung",
  "Decision" oder "Festgelegt".

`related_projects`:

* Projekte, die in den letzten Treffern erwaehnt werden.
* Spaeter: Projekte aus Link-Relationen oder gemeinsamen Tags.

`suggested_searches`:

* regelbasierte Hinweise, z. B. `Phase 4`, `deployment`, `backup`,
  `bugfix`, basierend auf Tags/Phasen.

### Warum kein LLM im Server?

Der Server soll als verlaessliche Quelle dienen, nicht als Blackbox.
KI-Clients koennen die strukturierten Daten selbst zusammenfassen. Das
haelt Bramble klein, auditierbar und offline-faehig.

## 5. Tool: `journal_digest`

### Empfehlung

Ein Digest-Tool fuer Zeitraeume:

```text
journal_digest(project=None, since="7d", until=None, tags=None)
```

### Anwendungsfaelle

* "Was ist in Elder-Berry diese Woche passiert?"
* "Was ist projektuebergreifend seit gestern passiert?"
* "Welche Sicherheits-/Deployment-Themen gab es in den letzten 30 Tagen?"
* "Welche offenen Punkte gibt es vor dem naechsten Deploy?"

### MVP-Ausgabe

```json
{
  "range": {
    "since": "...",
    "until": "..."
  },
  "projects": [],
  "counts_by_project": {},
  "counts_by_status": {},
  "entries": [],
  "open_items": [],
  "bugfixes": [],
  "decisions": []
}
```

Der Digest ist im MVP keine freie Textzusammenfassung, sondern eine
strukturierte Auswahl und Aggregation.

### Zeitraum-Syntax

MVP:

* `24h`
* `7d`
* `30d`
* ISO-Zeitstempel

Spaeter:

* `today`
* `this-week`
* `last-week`
* explizite lokale Zeitzone.

## 6. Tool: `journal_open_items`

### Empfehlung

Optionales kleines Tool, falls `journal_context` zu breit wird:

```text
journal_open_items(project=None, limit=50)
```

MVP-Regel:

* Liefert `status="in_arbeit"` nach neuestem Datum.

Spaeter:

* erkennt erledigende Eintraege ueber Link-Relationen,
* gruppiert nach Projekt, Phase und Tag,
* unterscheidet "offen", "blockiert", "wartet auf Host".

## 7. Admin-UI-Integration

Dashboard:

* Digest fuer `24h`, `7d`, `30d`.
* Offene Punkte projektuebergreifend.
* Letzte Entscheidungen.

Projektansicht:

* Kontextbox oben:
  * letzte Aktivitaet,
  * offene Punkte,
  * letzte Bugfixes,
  * relevante Tags.

Globale Suche:

* Suchfeld im Header.
* Filter: Projekt, Status, Tag, Zeitraum, Actor/Client.
* Treffer zeigen Projektkontext sichtbar an.

## 8. API-Design

Neue MCP-Tools sollten zunaechst read-only bleiben:

```text
journal_search_all(...)
journal_context(...)
journal_digest(...)
```

Rueckwaertskompatibilitaet:

* Bestehende Tools bleiben unveraendert.
* Neue Tools duerfen auf fehlende Metadaten tolerant reagieren.
* Wenn Tags/Links noch nicht existieren, arbeiten sie mit Status,
  Phase, Titel und Volltext.

## 9. Sicherheits- und Datenschutzregeln

* Keine Tokenwerte in Rueckgaben.
* Keine Admin-Session-Daten in Rueckgaben.
* Cross-project Suche bleibt read-only.
* Ergebnislimits hart begrenzen, z. B. maximal 100 Treffer.
* Queries validieren und bei fehlerhafter FTS-Syntax wie bisher leer
  oder kontrolliert antworten, nicht mit Tracebacks.

## 10. Implementierungsreihenfolge

1. `journal_search_all` auf Basis vorhandener FTS5-Tabelle.
2. `journal_digest` als reine Aggregation ueber Zeitraum und Status.
3. `journal_context` als kuratierte Kombination aus bestehenden
   Queries.
4. Admin-UI-Dashboard nutzt `journal_digest`.
5. AGENTS.md und AI-Client-Setup auf `journal_context` umstellen.
6. Spaeter Tags/Links/Actor-Metadaten integrieren.

## 11. Tests

Automatisiert:

* Cross-project Suche findet Treffer aus mehreren Projekten.
* Projektfilter begrenzt Treffer korrekt.
* Ergebnislimit wird eingehalten.
* Digest zaehlt Status und Projekte korrekt.
* Digest-Zeitraum filtert korrekt.
* Context liefert auch fuer leere Projekte sinnvolle leere Listen.
* Context funktioniert ohne Tags/Links.
* FTS-Fehler fuehren nicht zu Serverfehlern.

Manuell:

* Neuer KI-Client ruft `journal_context(project="elder-berry")` auf
  und erhaelt brauchbaren Startkontext.
* Dashboard zeigt Aktivitaet der letzten 7 Tage.
* Globale Suche findet ein Thema, das in Bramble und Elder-Berry
  vorkommt.

## 12. Empfehlung fuer den Start

Zuerst `journal_search_all`, weil es technisch nah an der bestehenden
Suche liegt und den projektuebergreifenden Nutzen sofort erhoeht.

Danach `journal_digest`, weil das Admin-UI und menschliche Uebersicht
direkt verbessert.

`journal_context` danach bauen, sobald klar ist, welche Datenstruktur
KI-Clients in der Praxis am besten verwerten.
