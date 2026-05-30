# Sicherheitsrichtlinie

## Sicherheitsmodell — bitte zuerst lesen

Bramble ist als **Single-Owner-Werkzeug** entworfen: ein Betreiber führt das
Journal für seine eigenen Projekte. Dieses Modell hat eine bewusste, aber
für Außenstehende überraschende Eigenschaft:

> **Lesen und Suchen sind projektübergreifend. Jedes gültige Token kann die
> Einträge ALLER Projekte lesen.** Nur das *Schreiben* (`journal_append`) ist
> an das Projekt des Tokens gebunden.

Das heißt konkret:

- Ein Token bedeutet effektiv **Lesezugriff auf das gesamte Journal**.
- Bramble bietet **keine Mandantentrennung** und ist **nicht** dafür geeignet,
  fremde, gegenseitig misstrauende Nutzer auf derselben Instanz zu bedienen.
- Gib Tokens nur an Parteien aus, denen du den Lesezugriff auf *alle*
  Projekte dieser Instanz anvertraust (z. B. deine eigenen Agenten/Projekte).
- Wer Mandantentrennung braucht, betreibt **getrennte Instanzen** mit
  getrennten Datenbanken.

Wenn du Bramble als gehosteten Mehr-Nutzer-Dienst betreiben willst, ist das
ohne tiefgreifende Änderungen (echte Mandanten-Lese-Isolation) **nicht**
sicher.

## Append-only und personenbezogene Daten

Das Datenmodell ist **append-only**: Es gibt bewusst keine Update-/Delete-
Tools; Korrekturen sind neue Einträge. Das ist für ein Entwicklungsjournal
ein Vorteil, steht aber im Konflikt mit Löschpflichten (z. B. DSGVO
Recht-auf-Löschung). Speichere daher keine personenbezogenen oder sonst
löschpflichtigen Daten, die du später entfernen können musst. Für einen
öffentlichen Dienst müsstest du eine eigene Lösch-/Purge-Strategie ergänzen.

## Betriebsempfehlungen

- HTTP-Transport ist authentifiziert (Bearer-Token) — **immer** hinter TLS
  und einem Reverse-Proxy betreiben, nie ungeschützt exponieren.
- Rate-Limit (pro Token/IP) und Fail2Ban aktiv lassen; siehe `deploy/`.
- Die Admin-UI nur lokal binden (Default `127.0.0.1`) und ausschließlich
  über einen SSH-Tunnel erreichen — **nicht** über einen öffentlichen Pfad.
- Tokens und das Argon2id-Admin-Secret liegen außerhalb des Repos
  (`secrets/`, per `.gitignore` ausgeschlossen). Niemals committen oder in
  Logs/Chatprotokolle schreiben.
- Nach Token-Rotation den MCP-Dienst neu starten (die Token-Datei wird beim
  Start gelesen).
- Schreibende Admin-Aktionen sind CSRF-geschützt und werden append-only in
  `admin_audit_events` protokolliert.

## Unterstützte Versionen

Aktuell wird nur der jeweils neueste Stand auf `main` mit Fixes versorgt.

## Sicherheitslücke melden

Bitte **keine** öffentlichen Issues für Sicherheitslücken anlegen. Nutze
stattdessen die private Meldefunktion von GitHub („Report a vulnerability"
im Tab *Security* des Repositories). Beschreibe Reproduktion, betroffene
Version/Commit und mögliche Auswirkung. Wir bestätigen den Eingang und
koordinieren einen Fix vor der Veröffentlichung.
