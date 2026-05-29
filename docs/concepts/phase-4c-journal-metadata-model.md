# Phase 4c - Journal-Metadatenmodell

Status: Konzept (2026-05-29).

Dieses Dokument beschreibt die Datenmodell-Erweiterungen, die Brambles
Journal deutlich nutzbarer machen sollen, ohne das append-only Prinzip
aufzugeben.

## 1. Ziel

Der aktuelle Kern ist tragfaehig:

* `journal_entries` speichert append-only Eintraege.
* `project` trennt die Projektraeume.
* `status`, `phase`, `title` und `content` reichen fuer den ersten
  Produktivbetrieb.
* FTS5 macht Volltextsuche moeglich.

Fuer den naechsten Nutzensprung fehlen strukturierte Metadaten:

* Projekte sollen sichtbar sein, auch wenn sie noch keine Eintraege
  haben.
* Korrekturen und Nachtraege sollen maschinenlesbar auf alte Eintraege
  zeigen.
* Tags sollen Suche, Dashboard und Digest verbessern.
* Eintraege sollen erkennen lassen, welcher Client oder Akteur sie
  erzeugt hat.

## 2. Leitprinzipien

* Append-only bleibt die Grundregel.
* Bestehende Eintraege werden nicht geaendert oder geloescht.
* Strukturierte Metadaten duerfen alte Eintraege ergaenzen, aber nicht
  ihren Inhalt umschreiben.
* Das MCP-Schreibmodell bleibt tokengebunden: ein Projekt-Token schreibt
  nur ins eigene Projekt.
* Alle Erweiterungen muessen rueckwaertskompatibel zu bestehenden
  Clients bleiben.

## 3. Projekt-Registry

### Empfehlung

Eine eigene Tabelle `projects` einfuehren.

### Nutzen

Aktuell erscheint ein Projekt erst, wenn es mindestens einen
Journal-Eintrag hat. Das ist fuer Admin-UI und Tokenverwaltung
unpraktisch: ein neu angelegtes Projekt mit Token sieht leer oder
unsichtbar aus.

Mit einer Registry kann Bramble anzeigen:

* Projekt existiert.
* Projekt hat noch keine Eintraege.
* Projekt hat ein aktives oder fehlendes Token.
* Projekt wurde importiert, ist aktiv oder archiviert.

### Tabellenentwurf

```sql
CREATE TABLE projects (
    name              TEXT PRIMARY KEY,
    display_name      TEXT,
    description       TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    default_phase     TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    archived_at       TEXT
);
```

Statuswerte:

* `active`
* `paused`
* `archived`

### Migrationsregel

Beim ersten Schema-Upgrade werden alle distinct `journal_entries.project`
Werte in `projects` eingefuegt.

Token-Projekte aus `tokens.json` oder spaeterer Token-Metadatenquelle
werden ebenfalls als Projekt sichtbar gemacht, auch ohne Eintraege.

## 4. Actor und Client

### Empfehlung

Eintraege sollen speichern, wodurch sie erzeugt wurden:

* Mensch im Admin-UI
* Codex
* Claude
* Import-Script
* Smoke-Test
* andere MCP-Clients

### Minimaler Tabellenentwurf

```sql
ALTER TABLE journal_entries ADD COLUMN actor TEXT;
ALTER TABLE journal_entries ADD COLUMN client TEXT;
ALTER TABLE journal_entries ADD COLUMN source TEXT;
```

Empfohlene Semantik:

* `actor`: wer fachlich handelt, z. B. `marcus`, `codex`, `claude`.
* `client`: technische Client-Kennung, z. B. `codex-desktop`,
  `admin-ui`, `import_journal_txt.py`.
* `source`: grobe Herkunft, z. B. `mcp`, `admin-ui`, `import`, `test`.

### Sicherheitsregel

Diese Felder sind Metadaten, keine Authentifizierungsquelle. Der Server
muss weiterhin aus dem Bearer-Token ableiten, in welches Projekt
geschrieben werden darf.

## 5. Tags und Kategorien

### Empfehlung

Tags als eigene Many-to-many-Struktur einfuehren, nicht als
kommagetrennte Zeichenkette in `journal_entries`.

### Nutzen

Tags machen Dashboard, Filter und Digest viel praeziser:

* `deployment`
* `security`
* `backup`
* `admin-ui`
* `import`
* `decision`
* `bug`
* `test`
* `docs`

### Tabellenentwurf

```sql
CREATE TABLE journal_tags (
    name        TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

CREATE TABLE journal_entry_tags (
    entry_id    INTEGER NOT NULL REFERENCES journal_entries(id),
    tag         TEXT NOT NULL REFERENCES journal_tags(name),
    PRIMARY KEY (entry_id, tag)
);
```

### Schreibmodell

MVP:

* Tags koennen beim `journal_append` optional mitgegeben werden.
* Admin-UI kann Tags beim Nachtrag/Bugfix setzen.
* Importer kann konservativ Tags ableiten, z. B. aus Titel und Phase.

Spaeter:

* Tag-Vorschlaege aus FTS/Suchbegriffen.
* Projektbezogene Default-Tags.

## 6. Beziehungen zwischen Eintraegen

### Empfehlung

Eine eigene Relationstabelle einfuehren.

### Motivation

Append-only bleibt richtig. Aber Korrekturen, Nachtraege und
Entscheidungsfolgen sollen maschinenlesbar sein.

Beispiele:

* Ein Bugfix korrigiert Eintrag `123`.
* Ein Nachtrag ergaenzt Eintrag `91`.
* Ein neuer Eintrag ersetzt fachlich einen alten Plan.
* Ein Deployment-Eintrag gehoert zu einer Entscheidung.

### Tabellenentwurf

```sql
CREATE TABLE journal_entry_links (
    from_entry_id  INTEGER NOT NULL REFERENCES journal_entries(id),
    to_entry_id    INTEGER NOT NULL REFERENCES journal_entries(id),
    relation       TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (from_entry_id, to_entry_id, relation)
);
```

Relationen:

* `corrects`
* `adds_context_to`
* `supersedes`
* `implements`
* `relates_to`

### UI-Regel

Die Admin-UI soll nicht "Bearbeiten" sagen, sondern:

* "Bugfix zu diesem Eintrag"
* "Nachtrag zu diesem Eintrag"
* "Ersetzt durch neuen Eintrag"

## 7. MCP-Erweiterungen

Rueckwaertskompatibel:

```text
journal_append(project, status, content, phase=None, title=None)
```

Optional erweitert:

```text
journal_append(
  project,
  status,
  content,
  phase=None,
  title=None,
  tags=None,
  links=None,
  actor=None,
  client=None,
  source=None
)
```

Wichtig:

* `actor`, `client` und `source` duerfen vom Server ueberschrieben oder
  ergaenzt werden.
* Fremde Projekt-Tokens duerfen keine Links erzeugen, die in fremde
  Projekte schreiben. Links auf gelesene fremde Eintraege sind
  fachlich erlaubt, solange der neue Eintrag im eigenen Projekt bleibt.

## 8. Admin-UI-Erweiterungen

Projektliste:

* Projekte aus `projects`, nicht nur aus `journal_entries`.
* Eintraege, Tokenstatus und letzter Eintrag als aggregierte Ansicht.
* Leere Projekte sichtbar mit Status "noch keine Eintraege".

Journalansicht:

* Tags als Filter.
* Actor/Client als Filter.
* Beziehungshinweise:
  * "korrigiert Eintrag #123"
  * "hat 2 Nachtraege"
  * "ersetzt durch #147"

Tokenansicht:

* Projekt-Registry und Token-Metadaten zusammenfuehren.
* Projekt ohne Token sichtbar machen.
* Token ohne Projekt-Registry-Eintrag als Wartungshinweis anzeigen.

## 9. Implementierungsreihenfolge

1. Schema-Migration fuer `projects`.
2. `project_overview()` auf Registry plus Journal-Aggregate umstellen.
3. Admin-UI-Projektliste auf neue Registry stuetzen.
4. Actor/Client/Source-Felder ergaenzen.
5. Tags-Tabellen und Tests.
6. Link-Tabelle und Korrektur/Nachtrag-Flows.
7. MCP-Tool-Parameter optional erweitern.

## 10. Tests

Automatisiert:

* Migration uebernimmt bestehende Projekte.
* Leeres Projekt erscheint in `journal_list_projects` oder einem neuen
  Projektlisten-Endpunkt.
* Token-Projekt ohne Eintrag erscheint in der Admin-UI.
* Tags werden validiert und dedupliziert.
* Link-Relationen verweisen auf existierende Eintraege.
* Append-only bleibt erhalten: Korrektur erzeugt neuen Eintrag und Link,
  veraendert aber den alten Eintrag nicht.
* Actor/Client/Source werden nicht als Auth-Quelle genutzt.

Manuell:

* Neues Projekt anlegen, Token erzeugen, vor erstem Eintrag in UI sehen.
* Ersten Eintrag schreiben, Projektstatus und Counts pruefen.
* Bugfix zu altem Eintrag anlegen und Link in UI sehen.

## 11. Empfehlung fuer den Start

Zuerst die Projekt-Registry bauen. Sie loest sofort den aktuellen
Verstaendnisknoten: ein Projekt kann existieren, ein Token haben und
trotzdem noch keine Journal-Eintraege besitzen.

Danach Actor/Client/Source ergaenzen, weil diese Metadaten billig sind
und spaeter bei Digest, Audit und Admin-UI viel Klarheit bringen.

Tags und Link-Relationen danach, weil sie staerker in UI und MCP-API
eingreifen.
