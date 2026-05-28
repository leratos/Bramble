# Bramble – Deployment-Runbook (Phase 3)

Dieses Runbook beschreibt, wie Bramble als abgesicherter,
öffentlich erreichbarer Dienst auf dem Ubuntu-/Plesk-Host
`journal.last-strawberry.com` in Betrieb genommen wird.

**Nicht** Teil von Phase 3 (→ Phase 4): Import bestehender
`journal.txt`-Dateien, Connector-Einrichtung in Claude.ai / Claude
Code. Hier geht es ausschließlich um Deployment und Härtung.

Die Designentscheidungen hinter den einzelnen Schritten stehen in
[`docs/concepts/phase-3-deployment.md`](concepts/phase-3-deployment.md).

Alle Befehle laufen als `root` bzw. via `sudo`, sofern nicht anders
angegeben.

---

## 0. Voraussetzungen

* Ubuntu-Host mit Plesk; die Domain `journal.last-strawberry.com` ist
  in Plesk angelegt und hat ein gültiges Let's-Encrypt-Zertifikat.
* Python ≥ 3.12 mit FTS5-fähigem `sqlite3`-Modul.
* Kommandozeilen-Tool `sqlite3` (Paket `sqlite3`) – für den
  Backup-Snapshot.
* `fail2ban` sowie `python3-systemd` (für das `systemd`-Backend von
  Fail2Ban).
* `borgbackup` ist bereits eingerichtet (bestehendes Backup-Script).

---

## 1. Service-User und Verzeichnisse

Ein dedizierter, nicht-privilegierter Service-User (Entscheidung H):

```sh
useradd --system --home-dir /opt/bramble --shell /usr/sbin/nologin bramble
```

Verzeichnis-Layout unter `/opt/bramble`:

| Pfad | Inhalt |
|---|---|
| `/opt/bramble/` | Repository (Code, `scripts/`, `deploy/`) |
| `/opt/bramble/.venv/` | virtuelle Python-Umgebung |
| `/opt/bramble/data/` | SQLite-DB `bramble.db` (+ WAL-Sidecars) |
| `/opt/bramble/secrets/` | `tokens.json`, `admin-ui.json` – **nie** im Repo |

---

## 2. Code und virtuelle Umgebung

Repository nach `/opt/bramble` bringen (klonen oder kopieren). Bei
privatem Repo bevorzugt per SSH-Deploy-Key:

```sh
git clone git@github.com:leratos/Bramble.git /opt/bramble
cd /opt/bramble
mkdir -p data secrets
python3 -m venv .venv
.venv/bin/pip install .
```

`pip install .` (nicht `-e`) installiert das Paket samt
Laufzeit-Abhängigkeiten (`fastmcp`, `python-json-logger`) fest in die
venv – passend zur schreibgeschützten Härtung der `systemd`-Unit.

Abschließend gehören alle Dateien dem Service-User:

```sh
chown -R bramble:bramble /opt/bramble
chmod 700 /opt/bramble/secrets
chmod 750 /opt/bramble/data
```

---

## 3. Datenbank initialisieren

```sh
sudo -u bramble BRAMBLE_DB_PATH=/opt/bramble/data/bramble.db \
    /opt/bramble/.venv/bin/python /opt/bramble/scripts/init_db.py
```

`JournalDB.initialize()` legt das Schema an und schaltet die DB auf
**WAL-Mode** (Entscheidung I). WAL bleibt im DB-Header gespeichert –
einmalig genügt. Das Script prüft außerdem die FTS5-Verfügbarkeit und
bricht mit Exit-Code 2 ab, falls FTS5 fehlt.

Danach die DB-Datei auf owner/group-readable beschränken:

```sh
chmod 640 /opt/bramble/data/bramble.db
chown bramble:bramble /opt/bramble/data/bramble.db
```

---

## 4. Projekt-Tokens erzeugen

Pro Projekt ein eigenes Bearer-Token (Entscheidung A). Für jedes
Projekt einmal aufrufen:

```sh
sudo -u bramble BRAMBLE_TOKENS_FILE=/opt/bramble/secrets/tokens.json \
    /opt/bramble/.venv/bin/python /opt/bramble/scripts/gen_token.py bramble
```

Das Script gibt das erzeugte Token **einmalig** auf stdout aus – dieser
Wert wird in die MCP-Konfiguration des jeweiligen KI-Tools eingetragen.
`tokens.json` wird mit Modus `600` angelegt; ein erneuter Aufruf für ein
bestehendes Projekt **rotiert** dessen Token.

Kontrolle:

```sh
ls -l /opt/bramble/secrets/tokens.json   # -> -rw------- bramble bramble
```

---

## 5. systemd-Unit installieren

```sh
cp /opt/bramble/deploy/bramble.service /etc/systemd/system/bramble.service
systemctl daemon-reload
systemctl enable --now bramble
```

Status und Logs prüfen:

```sh
systemctl status bramble
journalctl -u bramble -n 50 --no-pager
```

Der Prozess bindet laut Unit nur an `127.0.0.1:8765`; sämtliche
Konfiguration kommt aus den `Environment=`-Zeilen der Unit
(`BRAMBLE_*`-Variablen, aufgelöst über `CLI > Env > Default`). Logs
gehen als JSON auf stderr → journald.

Die Unit setzt zusätzlich `FASTMCP_ENV_FILE=/opt/bramble/nonexistent.env`.
Das verhindert, dass FastMCP bzw. `pydantic-settings` versehentlich
eine `.env` aus dem WorkingDirectory lädt und damit Brambles explizite
systemd-Konfiguration überlagert.

---

## 5b. Admin-UI systemd-Unit installieren (Phase 4b)

Die Admin-UI ist ein eigener Server und wird **nicht** in Plesk/Nginx
eingetragen. Zugriff erfolgt nur per SSH-Tunnel auf den Loopback-Bind
`127.0.0.1:8770`.

Nach einem Pull der Phase-4b-Änderungen die venv aktualisieren, damit
`bramble-admin`, Starlette/Jinja2/Uvicorn und `argon2-cffi` installiert
sind:

```sh
cd /opt/bramble
sudo -u bramble /opt/bramble/.venv/bin/pip install .
```

Admin-Secret erzeugen. Das Passwort wird interaktiv abgefragt und nicht
in die Shell-History geschrieben:

```sh
sudo -u bramble /opt/bramble/.venv/bin/python \
    /opt/bramble/scripts/gen_admin_secret.py \
    --output /opt/bramble/secrets/admin-ui.json
chmod 600 /opt/bramble/secrets/admin-ui.json
chown bramble:bramble /opt/bramble/secrets/admin-ui.json
```

Die Admin-UI verwendet dieselbe Token-Datei wie der MCP-Service:
`/opt/bramble/secrets/tokens.json`. Sie wird in Abschnitt 4 angelegt
und bleibt Besitzer `bramble:bramble`, damit die Tokenverwaltung
atomar ueber eine temporaere Nachbardatei schreiben kann.

Zeitstempel bleiben in der Datenbank UTC. Die Admin-UI formatiert sie
nur fuer die Anzeige in `BRAMBLE_ADMIN_TIME_ZONE` (Default in der Unit:
`Europe/Berlin`) und kuerzt auf Minute plus Zeitzonenkuerzel, z. B.
`2026-05-28 22:41 CEST`.

Unit installieren und starten:

```sh
cp /opt/bramble/deploy/bramble-admin.service \
    /etc/systemd/system/bramble-admin.service
systemctl daemon-reload
systemctl enable --now bramble-admin
```

Status, Logs und Loopback-Bind prüfen:

```sh
systemctl status bramble-admin
journalctl -u bramble-admin -n 50 --no-pager
ss -ltnp | grep ':8770'
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' \
    http://127.0.0.1:8770/
curl -s -o /dev/null -w '%{http_code}\n' \
    http://127.0.0.1:8770/login
```

Erwartung:

* `ss` zeigt ausschließlich `127.0.0.1:8770`, nicht `0.0.0.0:8770`.
* `/` antwortet ohne Login mit `303` nach `/login?next=/`.
* `/login` antwortet mit `200`.
* Nach Login ist `/tokens` erreichbar; bestehende Tokenwerte werden
  nicht angezeigt.
* Es gibt keinen Plesk-/Nginx-Pfad wie `/admin`.

Lokaler Zugriff vom eigenen Rechner:

```sh
ssh -L 8770:127.0.0.1:8770 lera@h2724315.stratoserver.net
```

Danach lokal im Browser öffnen:

```text
http://127.0.0.1:8770
```

Bei Produktivbetrieb soll SSH zusätzlich per `nftables` auf die
NordVPN Dedicated IP und dokumentierte Break-Glass-Ausnahmen
eingeschränkt werden. Firewall-Änderungen nur mit zweiter offener
SSH-Session und Rollback-Fenster ausrollen.

---

## 6. Nginx-Reverse-Proxy über Plesk

Plesk verwaltet vHost und TLS; Bramble liefert nur Zusatzanweisungen
(Entscheidung G). Den `location`-Block aus
[`deploy/plesk-nginx-directives.conf`](../deploy/plesk-nginx-directives.conf)
in Plesk eintragen:

> Domains → `journal.last-strawberry.com` → **Apache & nginx Settings**
> → Feld **„Additional nginx directives"**

Danach in Plesk übernehmen/anwenden. Der MCP-Endpunkt ist anschließend
unter `https://journal.last-strawberry.com/mcp/` erreichbar.

**Wichtig (X-Forwarded-For):** Die Direktiven setzen
`X-Forwarded-For` bewusst auf `$remote_addr` und **nicht** auf
`$proxy_add_x_forwarded_for`. So enthält der Header genau die echte
Client-IP; ein vom Client mitgeschickter, gefälschter Header wird
überschrieben. Bramble vertraut dem Header nur, weil der Request von
`127.0.0.1` (diesem Proxy) kommt.

---

## 7. Fail2Ban

```sh
cp /opt/bramble/deploy/fail2ban/bramble-filter.conf /etc/fail2ban/filter.d/bramble.conf
cp /opt/bramble/deploy/fail2ban/bramble-jail.conf   /etc/fail2ban/jail.d/bramble.conf
systemctl restart fail2ban
```

Kontrolle:

```sh
fail2ban-client status bramble
```

Der Filter matcht das JSON-Event `auth_failed`, das `AuthValidator`
bei jedem fehlgeschlagenen Auth-Versuch inklusive Client-IP loggt. Der
Jail liest per `backend = systemd` direkt das `bramble.service`-Journal
– deshalb wird `python3-systemd` benötigt.

Politik (Entscheidung F): `maxretry = 3`; erste Sperre **1 Stunde**,
danach eskalierend (`bantime.increment`) bis `bantime.maxtime` (hier
1 Jahr – Fail2Ban kennt keine echte Dauersperre, ein Jahr ist das
praktische Äquivalent).

Test des Filters gegen das Journal:

```sh
fail2ban-regex "journalctl -u bramble" /etc/fail2ban/filter.d/bramble.conf
```

---

## 8. Borg-Backup erweitern (WAL-sicher)

Eine reine Datei-Kopie einer **aktiven** WAL-Datenbank kann
inkonsistent sein (Entscheidung J). Das bestehende Borg-Script bekommt
daher einen **Pre-Backup-Schritt**, der einen konsistenten Snapshot
zieht; Borg sichert dann den Snapshot, nicht die Live-Datei.

Einmalig das Snapshot-Script ausführbar machen und ein Staging-
Verzeichnis anlegen:

```sh
chmod 755 /opt/bramble/deploy/bramble-backup-snapshot.sh
install -d -o bramble -g bramble -m 0750 /opt/bramble/backup-staging
```

Im Borg-Script **vor** dem `borg create`-Aufruf einfügen:

```sh
# Konsistenter Snapshot der (WAL-)Live-DB via SQLite-Online-Backup-API.
BRAMBLE_SNAPSHOT=$(/opt/bramble/deploy/bramble-backup-snapshot.sh)
```

Im `borg create`-Aufruf statt der Live-DB den Snapshot sichern:
`"$BRAMBLE_SNAPSHOT"` bzw. `/opt/bramble/backup-staging/bramble.db`
statt `/opt/bramble/data/bramble.db`. Die WAL-Sidecars
(`bramble.db-wal`, `bramble.db-shm`) werden **nicht** mitgesichert –
der `.backup`-Snapshot ist bereits ein vollständiger, in sich
konsistenter Stand.

Zusätzlich sollten die Secret-Dateien in das verschlüsselte
Borg-Backup: `/opt/bramble/secrets/tokens.json` und
`/opt/bramble/secrets/admin-ui.json`. Ohne `tokens.json` bleiben die
Journal-Daten zwar restaurierbar, aber alle eingerichteten
Connector-Tokens müssten nach einem Restore rotiert und in den Clients
neu eingetragen werden. Ohne `admin-ui.json` muss das Admin-Passwort
nach einem Restore neu gesetzt werden.

Nach dem ersten Backup einen Restore-Test gegen den frisch erzeugten
Archivstand machen. Wichtig: die Umleitung `>` muss in derselben
Root-Shell laufen wie `borg extract`, sonst schreibt die Shell als
normaler Benutzer nach `/tmp` und kann an Verzeichnisrechten scheitern.

```sh
sudo bash -c '
set -euo pipefail

export BORG_REPO="ssh://u570858@u570858.your-storagebox.de:23/./backups/server"
export BORG_RSH="ssh -i /root/.ssh/hetzner_storage_box"
export BORG_PASSPHRASE
BORG_PASSPHRASE=$(cat /root/.borg-passphrase)

rm -rf /tmp/bramble-restore-test
mkdir -p /tmp/bramble-restore-test

borg extract --stdout "$BORG_REPO::<archive>" opt/bramble/backup-staging/bramble.db \
    > /tmp/bramble-restore-test/bramble.db

sqlite3 /tmp/bramble-restore-test/bramble.db "PRAGMA integrity_check;"
sqlite3 /tmp/bramble-restore-test/bramble.db \
    "SELECT COUNT(*) FROM journal_entries;"
sqlite3 /tmp/bramble-restore-test/bramble.db \
    "SELECT COUNT(*) FROM journal_fts;"
ls -lh /tmp/bramble-restore-test/bramble.db
'
```

Erwartung: `integrity_check` gibt `ok` aus; die beiden Counts laufen
ohne Fehler. Die Zahlen müssen nicht identisch sein, weil FTS5 intern
mehrere Segment-/Indexzeilen verwaltet.

Verifizierter Lauf am 2026-05-26 gegen
`server-2026-05-26_20:39`: `integrity_check` → `ok`,
`journal_entries` → `2`, `journal_fts` → `2`,
Snapshot-Größe `32K`.

### Einbau in das aktuelle `/usr/local/bin/borg-backup.sh`

Stand 2026-05-26 läuft auf dem Host ein zentrales Script mit
`DUMP_DIR="/var/backups/db-dumps"` und einem einzigen `borg create`.
Für genau dieses Script:

1. Im Konfigurationsblock nach den Synapse-Variablen ergänzen:

```sh
# Bramble (SQLite WAL)
BRAMBLE_SNAPSHOT=""
BRAMBLE_TOKENS="/opt/bramble/secrets/tokens.json"
BRAMBLE_ADMIN_SECRET="/opt/bramble/secrets/admin-ui.json"
```

2. Nach dem Synapse-PostgreSQL-Dump und vor
   `log "DB-Dumps abgeschlossen:"` ergänzen:

```sh
log "SQLite Snapshot: bramble..."
BRAMBLE_SNAPSHOT=$(/opt/bramble/deploy/bramble-backup-snapshot.sh)
log "Bramble Snapshot: ${BRAMBLE_SNAPSHOT}"
```

3. Im `borg create`-Pfadblock ergänzen:

```sh
    "$BRAMBLE_SNAPSHOT"                                \
    "$BRAMBLE_TOKENS"                                  \
    "$BRAMBLE_ADMIN_SECRET"                            \
    /etc/systemd/system/bramble.service                \
    /etc/systemd/system/bramble-admin.service          \
    /etc/fail2ban/filter.d/bramble.conf                \
    /etc/fail2ban/jail.d/bramble.conf                  \
```

Die Live-Datei `/opt/bramble/data/bramble.db` bleibt bewusst **nicht**
in der Borg-Pfadliste. Auch `/opt/bramble/` als Ganzes sollte nicht
gesichert werden, weil dadurch die virtuelle Umgebung unter
`/opt/bramble/.venv/` unnötig ins Backup wandern würde.

---

## 9. Verifikation

End-to-End-Smoke-Test gegen den deployten Endpunkt (mit gültigem
Token eines Projekts):

```sh
python /opt/bramble/scripts/smoke_http.py \
    --url https://journal.last-strawberry.com/mcp/ \
    --token <bramble-token>
```

Erwartet: alle Checks grün, inklusive der Negativ-Tests (Aufruf ohne
Token sowie `journal_append` in ein fremdes Projekt werden
abgewiesen).

Zusätzlich kurz prüfen, dass ein Request **ohne** Token scheitert:

```sh
curl -s -X POST https://journal.last-strawberry.com/mcp/ \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 10. Token-Rotation und -Sperre

Bevorzugter Weg ab Phase 4b: per SSH-Tunnel in der Admin-UI unter
`/tokens`.

* **Erzeugen:** Projektname eintragen. Der neue Token wird genau in
  dieser Antwort einmalig angezeigt.
* **Rotieren:** Projekt in der Liste waehlen und "Rotieren" ausloesen.
  Der neue Token wird einmalig angezeigt.
* **Sperren:** Projekt in der Liste waehlen und "Entfernen" ausloesen.
* **Aktivieren:** Danach immer `systemctl restart bramble`, weil der
  MCP-Service `/opt/bramble/secrets/tokens.json` beim Start liest.

Fallback ohne Admin-UI:

```sh
sudo -u bramble BRAMBLE_TOKENS_FILE=/opt/bramble/secrets/tokens.json \
    /opt/bramble/.venv/bin/python /opt/bramble/scripts/gen_token.py <projekt>
systemctl restart bramble
```

---

## Sicherheitshinweise

* `tokens.json` und Zertifikate gehören **nie** ins Repository
  (`.gitignore` deckt `secrets/` und DB-Dateien ab).
* Das Rate-Limit ist in-memory: ein Neustart des Dienstes setzt alle
  Buckets zurück – bewusst akzeptiert.
* Deployment-Artefakte sind Linux-spezifisch und können auf einem
  Windows-Entwicklungsrechner nicht vollständig getestet werden;
  Schritte 5–9 immer auf dem Host verifizieren.
