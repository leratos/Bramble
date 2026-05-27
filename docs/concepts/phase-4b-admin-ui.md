# Phase 4b - Admin-UI

Status: Konzept (2026-05-27). Die offenen Architektur- und
Sicherheitsentscheidungen sind in Abschnitt 10 als Decision Records
festgehalten.

Dieses Dokument beschreibt die Admin-Oberflaeche fuer Bramble. Sie soll
die Bedienung von Projekten, Journal-Eintraegen und Projekt-Tokens
vereinfachen, ohne das MCP-Sicherheitsmodell aufzuweichen.

## 1. Zielbild

Die Admin-UI ist ein internes Werkzeug fuer Betrieb und Pflege von
Bramble:

* Projekte ansehen und ihren Zustand einschaetzen.
* Journal-Eintraege lesen, suchen und append-only ergaenzen.
* Projekt-Tokens erzeugen, rotieren und deaktivieren.
* Einen klaren Ueberblick ueber Aktivitaet, Backup-/Importstand und
  naechste Arbeitsschritte bekommen.

Bramble bleibt dabei append-only. Es gibt auch im Admin-UI kein echtes
Editieren oder Loeschen bestehender Journal-Eintraege. "Anpassen"
bedeutet: neuen `notiz`- oder `bugfix`-Eintrag erzeugen, der den alten
Eintrag referenziert.

## 2. Nicht-Ziele

* Kein oeffentliches Dashboard fuer fremde Nutzer.
* Keine Mehrbenutzer-Rollen im ersten Schritt.
* Kein direktes Bearbeiten historischer Journal-Eintraege.
* Keine Anzeige bestehender Token im Klartext. Tokens werden nur direkt
  nach der Erzeugung oder Rotation einmalig angezeigt.
* Keine Abhaengigkeit von Cloud-Diensten.

## 3. Sicherheitsmodell

Die Admin-UI bekommt ein staerkeres Schutzmodell als der normale
MCP-Endpunkt.

### 3.1 Lokale Bindung und SSH-Tunnel

Empfohlenes Expositionsmodell:

```text
Browser lokal -> SSH-Tunnel -> 127.0.0.1:<admin-port> auf dem Server
```

Die Admin-App bindet nur an Loopback:

```text
127.0.0.1:8770
```

Beispiel fuer den Zugriff:

```sh
ssh -L 8770:127.0.0.1:8770 lera@h2724315.stratoserver.net
```

Danach lokal oeffnen:

```text
http://127.0.0.1:8770
```

Es gibt im MVP keinen oeffentlichen Nginx- oder Plesk-Pfad wie
`/admin`.

### 3.2 NordVPN Dedicated IP

SSH-Zugriff auf den Server soll zusaetzlich auf die dedizierte
NordVPN-IP beschraenkt werden. Dadurch ist der Admin-Tunnel nur
erreichbar, wenn die VPN-Verbindung aktiv ist.

Entscheidung:

* Die Allowlist wird auf Netzwerkebene gepflegt, bevorzugt in der
  bestehenden `nftables`-Konfiguration.
* SSH bleibt per Key-Login erreichbar, aber nur von der NordVPN
  Dedicated IP und klar definierten Break-Glass-Ausnahmen.
* Es wird kein oeffentlicher Admin-Pfad hinter Plesk/Nginx gebaut.

Wichtig fuer die Umsetzung:

* Firewall-Aenderungen duerfen nur mit Rollback-Fenster ausgerollt
  werden, damit kein Lockout entsteht.
* Vor jeder produktiven Einschraenkung muss eine zweite offene SSH-
  Session bestehen.
* Der Notfallzugang wird in einem kurzen Runbook dokumentiert.

### 3.3 Admin-Passwort

Die UI verlangt zusaetzlich ein Admin-Login.

MVP-Vorschlag:

* Ein Admin-User.
* Passwort-Hash in `/opt/bramble/secrets/admin-ui.json`.
* Hash mit Argon2id ueber `argon2-cffi`.
* Serverseitige Sessions mit zufaelligem, opakem Session-Identifier im
  Cookie.
* Session-Cookie mit `HttpOnly`, `SameSite=Strict` und kurzer
  Lebensdauer. Bei spaeterem TLS zusaetzlich `Secure`.
* Logout-Funktion.
* Login-Rate-Limit ab MVP, nicht erst spaeter.

Spaetere Haertung:

* TOTP/2FA.
* Audit-Log fuer Admin-Aktionen.

## 4. Informationsarchitektur

Die UI besteht aus drei Bereichen.

### 4.1 Header

Oben liegt die Hauptnavigation:

* Hauptseite
* Projekte
* Token

Optional spaeter:

* Suche
* System
* Backup

### 4.2 Linke Seite

Die linke Seite nimmt ungefaehr ein Viertel der Breite ein und zeigt
eine anklickbare Projektuebersicht:

* Projektname
* Anzahl Journal-Eintraege
* letzter Eintrag
* Token-Status: vorhanden, fehlt, rotiert, deaktiviert
* Aktivitaetsindikator: heute, diese Woche, aelter

Ein Klick auf ein Projekt oeffnet rechts die Projektansicht.

### 4.3 Rechte Seite

Der rechte Bereich zeigt den Inhalt der aktuellen Auswahl:

* Dashboard-Statistik
* Projekt-Details
* Journal-Leser
* Korrektur-/Nachtragsformular
* Token-Verwaltung

## 5. Views

### 5.1 Hauptseite: Statistik

Inhalte:

* Anzahl Projekte
* Gesamtzahl Journal-Eintraege
* Eintraege der letzten 24 Stunden / 7 Tage / 30 Tage
* Projekte ohne Token
* zuletzt aktive Projekte
* letzte Import-/Backup-bezogene Eintraege

Nuetzliche Filter:

* Zeitraum
* Projekt
* Status

### 5.2 Projekte

Projekt-Detailansicht:

* Projekt-Metadaten: Name, Eintraege, letzter Eintrag
* Aktivitaetsverlauf
* Journal-Liste, neueste zuerst
* Suche im Projekt
* Filter nach Status und Phase
* Button "Nachtrag erstellen"
* Button "Bugfix zu diesem Eintrag erstellen"

Wichtig: Die Buttons erzeugen neue Journal-Eintraege. Sie veraendern
den alten Eintrag nicht.

### 5.3 Token

Token-Verwaltung:

* Projektliste mit Token-Status
* neues Projekt-Token erzeugen
* vorhandenes Token rotieren
* Token deaktivieren oder loeschen
* neuer Token wird einmalig angezeigt und danach nicht mehr sichtbar
* Hinweis, ob ein Service-Restart oder Token-Reload noetig ist

Aktueller technischer Stand:

* Tokens liegen in `/opt/bramble/secrets/tokens.json` als
  `project -> token`.
* Der Server liest die Datei beim Start.
* Nach Token-Aenderungen ist aktuell ein Restart von `bramble.service`
  noetig.

Technische Entscheidung:

* MVP liest/schreibt weiter `tokens.json`, damit der bestehende
  MCP-Server unveraendert funktioniert.
* Die UI fuehrt zusaetzlich Token-Metadaten ein, z. B. Erstellzeit,
  Rotationszeit, deaktiviert seit, Kommentar und letzter bekannter
  Status. Der Klartext-Token bleibt trotzdem nur einmalig sichtbar.
* Token-Aenderungen loesen im MVP keinen automatischen Service-Restart
  aus. Die UI zeigt den notwendigen Restart deutlich an und liefert den
  passenden Host-Befehl.
* Spaeter wird ein sicherer Token-Reload ohne systemd-Restart geprueft.
* Langfristige Haertung: Tokens at rest nur gehasht speichern und den
  Klartext ausschliesslich beim Erzeugen oder Rotieren anzeigen.

## 6. Backend-Architektur

Moegliche Umsetzung:

* Ein kleiner zusaetzlicher Admin-Server im bestehenden Python-Paket.
* Separater Entry-Point, z. B. `bramble-admin`.
* Eigene systemd-Unit, z. B. `bramble-admin.service`.
* Bindet nur an `127.0.0.1`.
* Greift direkt auf dieselbe SQLite-DB und Token-Datei zu.

Alternativ:

* Admin-UI als Route im bestehenden HTTP-Server.

Entscheidung:

* Separater Admin-Server. Dadurch bleibt der MCP-Server klein, stabil
  und auf Tool-Calls fokussiert.
* Umsetzung mit Starlette und serverseitig gerenderten Templates
  statt als Single-Page-App.
* Kein Frontend-Build-System im MVP.

Begruendung:

* Starlette passt nah an die bestehende ASGI/FastMCP-Welt, ohne den
  MCP-Server selbst zu vermischen.
* Serverseitige Templates reduzieren clientseitige Token- und
  State-Flows.
* Fuer ein internes Admin-Werkzeug ist ein ruhiges, formularbasiertes
  UI sicherer und wartbarer als eine fruehe SPA.

## 7. Admin-Aktionen

MVP-Aktionen:

* Projekte listen.
* Projektjournal lesen.
* Projektjournal durchsuchen.
* neuen Journal-Eintrag schreiben.
* Korrektur/Nachtrag zu bestehendem Eintrag schreiben.
* Token fuer Projekt erzeugen.
* Token fuer Projekt rotieren.
* Token fuer Projekt deaktivieren/loeschen.

Keine MVP-Aktionen:

* bestehende Journal-Eintraege editieren.
* bestehende Journal-Eintraege loeschen.
* Projekt-Namen umbenennen.
* Bulk-Operationen ueber mehrere Projekte.

## 8. Implementierungsplan

### Schritt 1: Konzept und Minimal-Schnitt

* Konzept finalisieren.
* Port, Pfade und systemd-Unit festlegen.
* Admin-Secret-Datei definieren.
* Starlette/Jinja2-Abhaengigkeiten festlegen.

### Schritt 2: Read-only MVP

* Admin-Login.
* Dashboard mit Statistik.
* Projektliste links.
* Projektansicht rechts.
* Journal lesen und suchen.
* Nur Loopback-Bindung.

### Schritt 3: Append-only Journal-Aktionen

* Nachtrag-Formular.
* Bugfix-Formular zu bestehendem Eintrag.
* Status-/Phase-Auswahl.
* Serverseitige Validierung identisch zum MCP-Layer.

### Schritt 4: Token-Verwaltung

* Token erzeugen.
* Token rotieren.
* Token deaktivieren/loeschen.
* Token nur einmalig anzeigen.
* Service-Restart oder Token-Reload ausloesen bzw. klar anzeigen.

### Schritt 5: Host-Haertung

* systemd-Unit fuer Admin-Server.
* Firewall-/SSH-Regeln fuer NordVPN Dedicated IP dokumentieren.
* Backup-Pfade fuer Admin-Secret und ggf. Token-Metadaten pruefen.
* Smoke-Test ueber SSH-Tunnel dokumentieren.
* Break-Glass-Runbook mit temporarer Firewall-Oeffnung dokumentieren.

## 9. Tests und Verifikation

Automatisierte Tests:

* Login erfolgreich/fehlerhaft.
* Nicht eingeloggte Requests werden abgewiesen.
* Projektliste entspricht `JournalDB.project_overview()`.
* Journal-Lesen und Suche respektieren Projektfilter.
* Nachtrag/Bugfix erzeugt neuen Eintrag und veraendert alten Eintrag
  nicht.
* Token-Erzeugung schreibt atomar.
* Token-Rotation ersetzt nur das Zielprojekt.
* Token-Loeschung betrifft nur das Zielprojekt.

Manuelle Host-Verifikation:

* Admin-Server bindet nur an `127.0.0.1`.
* Ohne SSH-Tunnel ist die UI nicht erreichbar.
* Mit SSH-Tunnel und Passwort ist die UI erreichbar.
* Token-Rotation funktioniert nach Service-Restart.
* Bestehende MCP-Clients koennen mit altem Token nach Rotation nicht
  mehr schreiben.
* Neuer Projekt-Token kann nur ins eigene Projekt schreiben, aber
  projektuebergreifend lesen/suchen.

## 10. Entscheidungen und Empfehlungen

Die folgenden Entscheidungen priorisieren Sicherheit zuerst und
Bedienbarkeit direkt danach.

### A) Admin-Server-Technik

Entscheidung:

* Separater Admin-Server, nicht als Route im MCP-Server.
* Umsetzung mit Starlette und Jinja2/serverseitigen Templates.
* Eigener Entry-Point `bramble-admin`.
* Eigene systemd-Unit `bramble-admin.service`.
* Bind ausschliesslich an `127.0.0.1:8770`.

Empfehlung:

* Kein FastMCP fuer die Admin-UI verwenden.
* Keine SPA und kein Frontend-Build-System im MVP.

Warum:

* Der MCP-Server bleibt klein und stabil.
* Der Admin-Server kann separat gestoppt, gehaertet und getestet werden.
* Serverseitige HTML-Views sind fuer ein internes Admin-Tool einfacher
  zu pruefen und reduzieren clientseitigen Sicherheitszustand.

### B) Passwort-Hashing

Entscheidung:

* Argon2id ueber `argon2-cffi`.
* Admin-Secret-Datei:

```text
/opt/bramble/secrets/admin-ui.json
```

* Rechte: Besitzer `bramble`, Gruppe `bramble`, Modus `0600` oder
  maximal `0640`, wenn eine getrennte Admin-Gruppe eingefuehrt wird.

Empfehlung:

* `argon2-cffi` als explizite Runtime-Abhaengigkeit aufnehmen, sobald
  die Admin-UI implementiert wird.
* Kein eigenes Passwort-Hashing bauen.
* Keine Passwort-Policy mit Sonderzeichenzwang; stattdessen lange
  Passphrase, Rate-Limit und spaeter TOTP.

Warum:

* Argon2id ist speicherhart und fuer Passwort-Hashes geeignet.
* NIST und OWASP betonen Salted/iterative Hashes bzw. moderne
  Password-Hashing-Verfahren und Rate-Limits.

### C) Sessions und Login-Schutz

Entscheidung:

* Serverseitige Sessions.
* Cookie enthaelt nur einen zufaelligen, opaken Session-Identifier.
* `HttpOnly`, `SameSite=Strict`, kurzer Idle-Timeout.
* Login-Rate-Limit im MVP.

Empfehlung:

* Session-Timeout: 30 Minuten Idle, 8 Stunden absolute Obergrenze.
* Nach Passwortaenderung alle Sessions invalidieren.
* Logout sichtbar im Header.

Warum:

* Ein Session-Cookie ist nach Login praktisch ein Ersatz fuer das
  Passwort. Deshalb sollte darin kein Admin-Zustand oder Tokeninhalt
  liegen.

### D) Token-Reload

Entscheidung:

* Im MVP kein automatischer Restart und kein automatischer Token-Reload.
* Die Token-UI schreibt atomar und zeigt danach klar:
  "Bramble-Service neu starten, damit die Aenderung aktiv wird."
* Die UI zeigt den konkreten Befehl, fuehrt ihn aber nicht selbst aus.

Empfehlung:

* Erst nach dem read-only MVP entscheiden, ob ein sicherer Reload
  gebaut wird.
* Bevorzugter spaeterer Weg: Serverinterner Reload der Token-Datei, ohne
  Shell-Aufruf aus der Admin-UI.

Warum:

* Ein Web-UI, das `systemctl restart` ausloest, braucht zusaetzliche
  lokale Rechte und vergroessert die Angriffsfolgen.
* Der manuelle Restart ist weniger bequem, aber fuer den MVP sicherer
  und gut verstaendlich.

### E) Backup-/Restore-Status im UI

Entscheidung:

* Ja, aber erst nach dem read-only Projekt-/Journal-MVP.
* Im ersten Schritt nur read-only Status anzeigen.

Empfehlung:

* Nicht direkt `borg` aus der UI ausfuehren.
* Stattdessen anzeigen:
  * letzte Backup-bezogene Journal-Eintraege,
  * Alter von `/opt/bramble/backup-staging/bramble.db`,
  * optional letzte Zeilen aus einem dedizierten, lesbaren Statusfile.

Warum:

* Backup-Status ist fuer Bedienbarkeit wertvoll.
* Direkte Borg-Kommandos im Webprozess wuerden Secrets und
  Betriebskomplexitaet in die UI ziehen.

### F) Admin-Audit-Log

Entscheidung:

* Eigenes append-only Audit-Log, nicht nur normale Journal-Eintraege.
* Zusaetzlich duerfen wichtige Admin-Aktionen als Bramble-Journal-Notiz
  zusammengefasst werden.

Empfehlung:

* Neue SQLite-Tabelle `admin_audit_events`.
* Felder:
  `id`, `timestamp`, `actor`, `action`, `target_type`, `target`,
  `result`, `request_id`, `details_json`.
* Keine Tokenwerte, Passwortdaten oder Session-IDs loggen.

Warum:

* Audit-Ereignisse brauchen andere Garantien als Projektjournal-
  Eintraege.
* Token-Rotation, Login-Fehler und Admin-Aktionen sollen strukturiert
  auswertbar sein.

### G) Notfallzugang

Entscheidung:

* Primaerer Zugriff: NordVPN Dedicated IP -> SSH -> Tunnel -> Passwort.
* Break-Glass nur ueber Provider-/Server-Konsole oder eine bewusst
  dokumentierte, temporaere Firewall-Oeffnung.
* Keine dauerhaft offene zweite Admin-Route.

Empfehlung:

* Break-Glass-Runbook anlegen:
  1. Ueber Provider-Konsole anmelden.
  2. Aktuelle Quell-IP temporaer fuer SSH erlauben.
  3. Timer/Rollback setzen, z. B. 30 bis 60 Minuten.
  4. Zugriff wieder entfernen.
  5. Ereignis im Bramble-Journal dokumentieren.

Warum:

* Eine permanente Fallback-IP oder ein oeffentlicher Admin-Pfad waere
  bequem, aber eine dauerhafte zusaetzliche Angriffsflaeche.

### H) Firewall-Ort

Entscheidung:

* Bevorzugt `nftables`, weil der Server bereits nftables-Konfiguration
  nutzt und diese im Backup-Konzept enthalten ist.
* Plesk-Firewall nur verwenden, wenn sie die bestehende nftables-
  Struktur nicht ueberschreibt oder unklar macht.

Empfehlung:

* SSH-Allowlist mit einem getesteten Rollback-Script ausrollen.
* Vor Anwendung immer zweite SSH-Session offen halten.
* Aenderung erst lokal in einem Konzept-/Deploy-Dokument beschreiben,
  dann auf dem Host anwenden.

Warum:

* Firewall-Fehler koennen direkten Lockout verursachen. Reproduzierbare
  nftables-Regeln sind sicherer als Klick-Konfiguration ohne klares
  Versionsbild.

## 11. Quellen und Leitplanken

Die Entscheidungen orientieren sich an:

* [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html):
  Passwort-Verifier sollen gehashte Secrets speichern und Rate-Limiting
  gegen Online-Rateversuche einsetzen.
* [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html):
  Authentifizierung, Login-Schutz und MFA/2FA als Haertung.
* [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html):
  Session-IDs sind nach Login sicherheitskritisch und brauchen geeignete
  Cookie- und Lifecycle-Kontrollen.

## 12. Entscheidungsvorschlag fuer den Start

Fuer den ersten Umsetzungsschritt:

* Admin-Server separat vom MCP-Server.
* Starlette + Jinja2, serverseitig gerendert.
* Bind nur an `127.0.0.1:8770`.
* Zugriff nur per SSH-Tunnel.
* SSH-Zugriff auf NordVPN Dedicated IP einschraenken.
* Ein Admin-Passwort mit Argon2id-Hash-Datei in
  `/opt/bramble/secrets`.
* Login-Rate-Limit direkt im MVP.
* Zuerst read-only Dashboard und Projektansicht bauen.
* Danach append-only Journal-Aktionen.
* Token-Verwaltung erst danach, weil sie sicherheitskritischer ist.
