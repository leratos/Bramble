# Phase 4e - Journal-Workflows und Rollout

Status: Aktiv in Umsetzung (2026-05-29).

Dieses Dokument beschreibt, wie die erweiterten Journal-Funktionen aus
Phase 4c und 4d in der Praxis genutzt werden sollen: im Admin-UI, in
KI-Agenten-Anweisungen und im Betrieb mehrerer Projekte.

## 1. Ziel

Die neuen Funktionen sollen nicht nur technisch existieren, sondern im
Alltag helfen:

* Neue Projekte sauber anlegen.
* Leere Projekte verstehen.
* Eintraege konsistent taggen.
* Korrekturen nachvollziehbar schreiben.
* KI-Sessions mit brauchbarem Kontext starten.
* Projektuebergreifende Themen schnell finden.
* Wochen-/Deploy-Ueberblicke erstellen.

## 2. Leitprinzipien

* Wenige klare Workflows sind wichtiger als viele Optionen.
* Sicherheit bleibt Vorrang: Tokens und Admin-Secrets werden nicht
  angezeigt oder geloggt.
* Das Journal bleibt append-only.
* Fuer Agents sollen Regeln kurz und wiederholbar sein.
* Admin-UI und MCP-Tools sollen dieselben Konzepte verwenden:
  Projekt, Status, Phase, Tags, Links, Actor/Client.

## 3. Projekt-Lifecycle

### Empfehlung

Ein Projekt bekommt einen sichtbaren Lifecycle:

1. `planned`
2. `active`
3. `paused`
4. `archived`

MVP kann intern mit `active`, `paused`, `archived` starten. `planned`
ist optional, aber fuer Projekte ohne Token oder ohne erste Eintraege
nuetzlich.

### Admin-Workflow

Projekt anlegen:

1. Name im kebab-case eingeben, z. B. `elder-berry`.
2. Optional Display-Name und Beschreibung setzen.
3. Optional Token direkt erzeugen.
4. Projekt erscheint sofort in der linken Projektliste.
5. Wenn keine Eintraege vorhanden sind, zeigt die UI:

```text
Noch keine Journal-Eintraege.
Naechster sinnvoller Schritt: ersten Kontext- oder Import-Eintrag
schreiben.
```

### KI-Workflow

Wenn `journal_context(project)` leer ist:

* Das ist kein Fehler.
* Agent soll einen kurzen Initialeintrag schreiben, sobald echte Arbeit
  beginnt.
* Kein reiner Smoke-Eintrag.

## 4. Eintragsarten und Status

Bestehende Statuswerte bleiben:

* `in_arbeit`
* `abgeschlossen`
* `notiz`
* `bugfix`

Empfohlene Nutzung:

* `in_arbeit`: gestartete Arbeit mit offenem naechstem Schritt.
* `abgeschlossen`: fertig verifizierter Arbeitsblock.
* `notiz`: Entscheidung, Betriebsereignis, Kontext, Nachtrag ohne
  Fehlerkorrektur.
* `bugfix`: Korrektur eines Fehlers, inklusive Korrektur zu altem
  Journal-Eintrag.

Nicht einfuehren:

* Zu viele neue Statuswerte wie `todo`, `blocked`, `review`, `draft`.
  Solche Informationen gehoeren vorerst in Tags oder Inhalt.

## 5. Tagging-Regeln

### Empfehlung

Mit einem kleinen kontrollierten Tag-Vokabular starten.

Basis-Tags:

* `decision`
* `deployment`
* `security`
* `backup`
* `import`
* `admin-ui`
* `test`
* `docs`
* `bug`
* `token`
* `agent`

Regeln:

* Tags sind lowercase kebab-case.
* Maximal 5 Tags pro Eintrag im MVP.
* Tags ergaenzen Status und Phase, ersetzen sie aber nicht.
* Admin-UI bietet Vorschlaege an, erlaubt aber spaeter eigene Tags.

### Beispiele

Deployment-Eintrag:

```text
status: abgeschlossen
phase: Phase 4b
tags: deployment, admin-ui, test
```

Token-Rotation:

```text
status: notiz
phase: Phase 4
tags: token, security
```

Korrektur:

```text
status: bugfix
tags: bug, docs
relation: corrects #123
```

## 6. Korrektur-Workflow

### Empfehlung

Die Admin-UI fuehrt Nutzer ueber konkrete Aktionen:

* "Nachtrag erstellen"
* "Bugfix zu diesem Eintrag"
* "Eintrag fachlich ersetzen"

Keine Aktion heisst "Bearbeiten".

### Ablauf: Bugfix zu Eintrag

1. Nutzer oeffnet alten Eintrag.
2. Klick auf "Bugfix zu diesem Eintrag".
3. Formular ist vorbefuellt:
   * Status `bugfix`
   * Link `corrects -> <alter eintrag>`
   * Titelvorschlag `Korrektur zu <alter Titel>`
4. Nutzer beschreibt:
   * Was war falsch?
   * Was ist korrekt?
   * Welche Auswirkung hat das?
5. Speichern erzeugt neuen Eintrag plus Link-Relation.

### Anzeige

Alter Eintrag:

```text
Dieser Eintrag wurde durch #147 korrigiert.
```

Neuer Eintrag:

```text
Korrigiert Eintrag #123.
```

## 7. KI-Agenten-Workflow

### Empfehlung

AGENTS.md pro Projekt auf `journal_context` umstellen, sobald das Tool
existiert.

Aktueller Start:

```text
journal_read(project="<projekt>", n=20)
```

Ziel-Start:

```text
journal_context(project="<projekt>", n_recent=10)
```

Bei unklarem Thema:

```text
journal_search_all(query="<thema>", limit=20)
```

Am Ende substanzieller Arbeit:

```text
journal_append(project="<projekt>", ...)
```

Optional spaeter:

* Tags mitgeben.
* Actor/Client automatisch setzen.
* Links auf korrigierte oder ergaenzte Eintraege setzen.

## 8. Admin-UI-Views

### Dashboard

Soll zuerst beantworten:

* Was ist seit 24h / 7d / 30d passiert?
* Welche Projekte sind aktiv?
* Welche Projekte haben keine Eintraege?
* Welche Projekte haben kein Token?
* Welche offenen `in_arbeit`-Eintraege gibt es?
* Welche Bugfixes gab es zuletzt?

### Projektseite

Oben:

* Projektstatus
* Tokenstatus
* letzter Eintrag
* offene Eintraege
* haeufigste Tags

Darunter:

* Journal-Liste
* Suche
* Filter nach Status, Tag, Actor/Client, Zeitraum
* Buttons fuer Nachtrag/Bugfix

### Globale Suche

Header-Suche ueber alle Projekte:

* Query
* Projektfilter
* Statusfilter
* Tagfilter
* Zeitraum

Treffer muessen Projekt und Timestamp klar anzeigen.

## 9. Import-Workflow

Legacy-Importe sollen die neuen Konzepte nutzen, aber konservativ.

Empfehlung:

* Importer legt Projekt in `projects` an, falls es fehlt.
* Importer setzt `source="import"` und `client="import_journal_txt.py"`.
* Importer darf Tags nur konservativ ableiten.
* Importer erstellt keine Link-Relationen im MVP.
* Nach Import schreibt der Nutzer oder Agent einen Abschluss-Eintrag
  mit Ergebnis und Verifikation.

## 10. Rollout-Plan

### Schritt 1: Projekt-Registry sichtbar machen

* Schema-Migration.
* Admin-UI-Projektliste nutzt Registry.
* Token-Projekte ohne Eintraege werden sichtbar.

### Schritt 2: Actor/Client/Source

* `journal_append` speichert Metadaten.
* Admin-UI setzt `source="admin-ui"`.
* Importer setzt `source="import"`.
* MCP-Clients setzen oder erhalten `client`.

### Schritt 3: Cross-project Suche

* `journal_search_all`.
* Admin-UI Header-Suche.
* AGENTS.md Empfehlung bei unklarem Kontext.

### Schritt 4: Digest und Context

* `journal_digest`.
* Dashboard nutzt Digest.
* `journal_context`.
* Projekt-AGENTS.md auf `journal_context` umstellen.

### Schritt 5: Tags und Links

* Tags einfuehren.
* Admin-UI Tag-Filter.
* Korrektur-/Nachtrag-Link-Workflow.
* Digest beruecksichtigt Tags und Links.

## 11. Tests

Workflow-Tests:

* Neues Projekt ohne Eintraege ist in Admin-UI sichtbar.
* Neues Projekt ohne Eintraege liefert leeren, aber gueltigen Context.
* Erster Eintrag aktualisiert Projektuebersicht.
* Admin-Bugfix erzeugt neuen Eintrag und Link.
* Global Search findet Treffer ueber mehrere Projekte.
* Digest zaehlt neue Eintraege nach Zeitraum.
* Importer setzt `source="import"`.
* Agenten-Anweisung mit `journal_context` funktioniert fuer ein Projekt
  mit und ohne Eintraege.

## 12. Betriebsregeln

* Keine manuellen DB-Edits fuer Projekt-Metadaten, wenn Admin-UI oder
  Scripts existieren.
* Token-Rotation bleibt auditierbar.
* Admin-UI zeigt keine Tokenwerte ausser direkt nach Erzeugung oder
  Rotation.
* Neue Projekt-AGENTS.md-Dateien sollen klar benennen:
  * Projektname,
  * Startkontext-Tool,
  * Append-only-Regel,
  * keine Pflege von `docs/journal.txt`.

## 13. Empfehlung fuer den Start

Mit Projekt-Registry und Actor/Client/Source beginnen. Das verbessert
sofort Verstaendlichkeit und Auditierbarkeit, ohne das Schreibmodell zu
veraendern.

Danach `journal_search_all` und `journal_digest`, weil diese beiden
Funktionen fuer Menschen und KI-Clients sofort sichtbar Mehrwert
bringen.

Tags und Link-Relationen zuletzt, weil sie die UI-Workflows staerker
veraendern und sauber eingefuehrt werden sollten.

## 14. Konkreter Betriebsworkflow (MVP)

Dieser Abschnitt definiert den verbindlichen Tagesablauf fuer den
Admin-Betrieb und KI-Agenten, damit Open-Items und Session-Kontext
konsistent bleiben.

### A) Session-Start (Agent)

1. Immer zuerst:

```text
journal_context(project="<projekt>", n_recent=10)
```

1. Bei unklarem oder cross-project Thema:

```text
journal_search_all(query="<thema>", limit=20)
```

1. Falls aktueller Arbeitsblock neu startet: sofort ein sauberer
  `in_arbeit`-Eintrag mit klarem Scope und naechstem Schritt.

### B) Waehrend der Arbeit

1. Relevante Entscheidungen als `notiz` mit Tag `decision` festhalten.
1. Fehlerkorrekturen immer als `bugfix`, niemals alten Eintrag aendern.
1. Deployment/Backup/Token-Ereignisse als `notiz` oder
  `abgeschlossen` mit passenden Tags (`deployment`, `backup`,
  `token`, `security`).

### C) Session-Ende (Agent/Admin)

1. Jeder substanzielle Arbeitsblock endet mit `journal_append`.
1. Wenn ein zuvor gestarteter Block fertig ist: Abschluss-Eintrag mit
  `status="abgeschlossen"` plus kurzer Verifikationsnotiz (Tests,
  Smoke, Hostcheck).
1. Offene Punkte bleiben explizit in einem `in_arbeit`-Eintrag mit
  naechstem Schritt sichtbar.

### D) Tagesabschluss im Admin-UI

1. Dashboard pruefen: `24h`/`7d` Aktivitaet, offene Punkte, Bugfixes.
1. Projektseiten mit vielen historischen `in_arbeit`-Eintraegen
   stichprobenartig nachfassen. Dabei gilt: keine Mutation alter
   Eintraege, stattdessen klaerende `notiz`/`abgeschlossen`-Nachtraege.
1. Bei Token-Aktionen (`create/rotate/delete`) Audit-Trail und
  Service-Neustart-Fenster dokumentieren.

### E) Qualitaetsregeln (DoD)

Ein Arbeitspaket gilt erst als "sauber abgeschlossen", wenn alle Punkte
erfuellt sind:

1. Code/Config ist committed.
1. Relevante Tests oder Smoke-Checks sind ausgefuehrt und genannt.
1. Journal-Eintrag wurde append-only geschrieben.
1. Offene Folgearbeit ist explizit als naechster Schritt dokumentiert
  (kein implizites Wissen).
