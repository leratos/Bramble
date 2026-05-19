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
mkdir -p /opt/bramble/data /opt/bramble/secrets
```

Verzeichnis-Layout unter `/opt/bramble`:

| Pfad | Inhalt |
|---|---|
| `/opt/bramble/` | Repository (Code, `scripts/`, `deploy/`) |
| `/opt/bramble/.venv/` | virtuelle Python-Umgebung |
| `/opt/bramble/data/` | SQLite-DB `bramble.db` (+ WAL-Sidecars) |
| `/opt/bramble/secrets/` | `tokens.json` – **nie** im Repo |

---

## 2. Code und virtuelle Umgebung

Repository nach `/opt/bramble` bringen (klonen oder kopieren), z. B.:

```sh
git clone https://github.com/leratos/Bramble.git /opt/bramble
cd /opt/bramble
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

Einmalig ein Staging-Verzeichnis anlegen:

```sh
mkdir -p /opt/bramble/backup-staging
chown bramble:bramble /opt/bramble/backup-staging
```

Im Borg-Script **vor** dem `borg create`-Aufruf einfügen:

```sh
# Konsistenter Snapshot der (WAL-)Live-DB via SQLite-Online-Backup-API.
sqlite3 /opt/bramble/data/bramble.db \
    ".backup '/opt/bramble/backup-staging/bramble.db'"
```

Im `borg create`-Aufruf statt der Live-DB den Snapshot sichern, also
`/opt/bramble/backup-staging/bramble.db` (statt
`/opt/bramble/data/bramble.db`) in die Pfadliste aufnehmen. Die
WAL-Sidecars (`bramble.db-wal`, `bramble.db-shm`) werden **nicht**
mitgesichert – der `.backup`-Snapshot ist bereits ein vollständiger,
in sich konsistenter Stand.

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

* **Rotieren:** `scripts/gen_token.py <projekt>` erneut aufrufen –
  überschreibt das Token des Projekts. Danach
  `systemctl restart bramble` (die Token-Datei wird beim Start
  gelesen). Das neue Token im KI-Tool eintragen.
* **Sperren:** Den Projekt-Eintrag aus
  `/opt/bramble/secrets/tokens.json` entfernen, dann
  `systemctl restart bramble`.

---

## Sicherheitshinweise

* `tokens.json` und Zertifikate gehören **nie** ins Repository
  (`.gitignore` deckt `secrets/` und DB-Dateien ab).
* Das Rate-Limit ist in-memory: ein Neustart des Dienstes setzt alle
  Buckets zurück – bewusst akzeptiert.
* Deployment-Artefakte sind Linux-spezifisch und können auf einem
  Windows-Entwicklungsrechner nicht vollständig getestet werden;
  Schritte 5–9 immer auf dem Host verifizieren.
