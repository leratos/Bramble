# Phase 4f - Robuste Open-Items im append-only-Journal

Status: Umgesetzt (2026-05-30). Erweitert Phase 4e.

## 1. Anlass

Das Journal ist append-only: der Abschluss einer Arbeit wird als *neuer*
Eintrag (`status="abgeschlossen"`) geschrieben; der urspruengliche
`in_arbeit`-Eintrag wird nie veraendert. Ein naiver
`status='in_arbeit'`-Filter meldet damit jeden je gestarteten Eintrag fuer
immer als "offen".

Befund (2026-05-30, Projekt `berry-gym`): `journal_open_items` lieferte 16
"offene" Items (Phasen 5/6/7/21/24/29.x ...), von denen real nur ~4 offen
waren. Zusaetzlich war `journal_context.open_items` mit `journal_open_items`
inkonsistent: live `bramble` -> `journal_open_items=0`, aber
`journal_context.open_items=6` (erledigte Phase-1..4-Start-Eintraege).

## 2. Diagnose

* `journal_open_items` war **kein** reiner Status-Filter mehr: seit den
  Commits `51501ae` ff. (2026-05-29) gibt es bereits eine Closure-Inferenz
  (Link / `#x -> #y`-Text / gleiche Phase / gleicher Titel). Sie ist
  deployed und wirkt bei disziplinierten Projekten (`bramble` -> 0).
* Sie ist aber auf metadaten-armen Bestandsdaten (`berry-gym`) faktisch
  inert: die Alt-Eintraege tragen weder `links` noch konsistente
  `phase`/`title`, also greift weder der Link- noch der Heuristik-Pfad.
* `journal_context.open_items` nutzte einen anderen Pfad: `digest.open_items`
  = roher `in_arbeit`-Slice im 30-Tage-Fenster, ohne Closure-Filter. Das
  over-reportet resolved-Items im Fenster und droppt echt offene Punkte
  aelter als 30 Tage (haengt am Import-Datum).
* Tiefere Ursache (Achse 2): die wirklich offene Arbeit (`berry-gym` 26,
  27, 28, 31.3) war gar nicht als `in_arbeit` kodiert, sondern nur in der
  "Naechste Aktion"-Spur und in `Status:`-Headern der Konzept-Docs. Das
  kann keine Query-Heuristik finden.

## 3. Loesung (zwei Achsen)

### Achse 1 - Over-Reporting (query-seitig)

* **Explizite Close-Semantik per Link (`resolves`)** als sauberer Weg nach
  vorn: der Abschluss-Eintrag verlinkt `resolves -> <offener Eintrag>`.
  Hoechste Konfidenz, append-only-konform.
* **Provenance + Staleness** im Read-Modell (`OpenItemView`): jeder
  `in_arbeit`-Eintrag wird als `open` | `stale` | `resolved` klassifiziert
  und traegt `resolution_reason` (`link` | `text` | `title` | `phase`),
  `resolved_by_id`, `age_days`. Jede Unterdrueckung ist nachvollziehbar.
* **Heuristik eingedaemmt**: die fuzzy Title/Phase-Schliesser akzeptieren
  nur noch `abgeschlossen`/`bugfix`; `notiz` schliesst nicht mehr (eine
  Notiz ist kein Abschluss). Explizite Pfade (Link inkl. `resolves`,
  `#x -> #y`-Text) bleiben breit (`abgeschlossen`/`bugfix`/`notiz`), weil
  sie Autor-Absicht sind.
* **Staleness statt hartem Schliessen** fuer Bestandsdaten: unresolved und
  aelter als `stale_after_days` (Default 30) wird als `stale` markiert,
  nicht ausgeblendet. Lieber over- als under-reporten - under-reporting
  macht unsichtbar Schaden.
* **`journal_context` vereinheitlicht**: der `open_items`-Slice nutzt
  dieselbe Closure-Inferenz, kein 30-Tage-Fenster mehr.

### Achse 2 - Under-Reporting (konventions-seitig)

Das Tool kann nur finden, was als `in_arbeit` kodiert ist. Echter Backlog
muss als schlanker `in_arbeit`-Eintrag mit klarem naechsten Schritt
gefuehrt werden (Phase 4e §14.C.3). Konzept-Doc-`Status:`-Header bleiben
die menschliche Wahrheit, sind aber nicht maschinenlesbar.

## 4. API

`journal_open_items(project=None, limit=50, include_resolved=False,
stale_after_days=30)`:

* Default: `open` + `stale` (resolved ausgeblendet).
* `include_resolved=True`: zeigt auch resolved-Items samt Begruendung.
* Output-Felder zusaetzlich zum Entry: `open_state`, `resolution_reason`,
  `resolved_by_id`, `age_days`.

Rueckwaertskompatibel: bestehende Entry-Felder bleiben Top-Level;
`JournalDB.open_items()`/`open_item_count()` behalten ihre Signatur (Admin).

## 5. Entscheidungen

* D1: Kein neuer Status (`todo`/`blocked`) - Phase 4e §4. `resolves`-Link
  ist die append-only-Variante derselben Idee.
* D2: Default-Verhalten nicht still aendern - Provenance/Staleness additiv,
  `include_resolved`/`stale_after_days` als Parameter.
* D3: Kein harter Backfill der Bestandsdaten. Staleness deckt Altlasten zur
  Laufzeit ab; `resolves` wirkt vorwaerts.
* D4: Praezedenz der Provenance `link > text > title > phase`; bei
  Gleichstand gewinnt der juengste Resolver (deterministisch).

## 6. Bekannte Restrisiken

* Phase-Heuristik bei groben Phasen-Buckets kann mehrere offene Subtasks
  derselben Phase faelschlich als resolved markieren. Mitigation:
  `resolution_reason="phase"` ist sichtbar und mit `include_resolved=True`
  auditierbar; fuer harte Sicherheit den `resolves`-Link nutzen.
* FTS5-Randbefund (stille leere Liste bei malformed Query wie `31.3`) ist
  bewusst NICHT Teil dieser Phase.

## 7. Deployment

Die Schema-Migration (`journal_entry_links`-CHECK um `resolves` erweitern)
laeuft host-seitig beim naechsten Deploy + `bramble.service`-Neustart.
Bis dahin lehnt die Produktiv-DB `resolves`-Links ab - bis zum Deploy in
`journal_append` keine `resolves`-Links gegen den Live-Host verwenden.
