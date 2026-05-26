# Maintenance Backlog

Status: laufende technische Schulden und spätere Härtungsaufgaben.

Diese Liste ist kein Phasenplan. Sie sammelt Punkte, die bewusst nicht
in den aktuellen Arbeitsschnitt gehören, aber nicht verloren gehen
sollen.

## FastMCP-Egress beim Serverstart

Beobachtung vom Host-Deploy am 2026-05-26: FastMCP 3.3.1 macht beim
Serverstart einen ausgehenden HTTPS-Request an `pypi.org`
(Versions-Check). Das ist kein unmittelbares Sicherheitsloch im
aktuellen Threat-Modell, widerspricht aber dem self-hosted-Anspruch.

Mögliche Lösungspfade:

* FastMCP-/pydantic-settings-Schalter finden, der den Versions-Check
  deaktiviert.
* Falls es keinen sauberen Schalter gibt: systemd-Egress härten, z. B.
  mit `IPAddressDeny=any` und gezielten `IPAddressAllow=`-Regeln für
  Loopback. Das braucht einen Host-Test, weil der Service weiterhin
  `127.0.0.1:8765` bedienen muss.

Akzeptanzkriterium:

* `systemctl restart bramble` erzeugt keinen ausgehenden Request ins
  Internet.
* HTTP-Smoke gegen `https://journal.last-strawberry.com/mcp/` bleibt
  grün.

## Optionaler Fail2Ban-Filter für 404-Floods

Der aktuelle Bramble-Jail sperrt nur `auth_failed`-Events aus der App.
Das ist bewusst eng und robust. Bot-Scans auf fremde Pfade wie
`/rdweb/` werden derzeit nicht vom Bramble-Jail verarbeitet.

Nur aufnehmen, wenn die Nginx-/Plesk-Logs zeigen, dass diese Scans
operativ stören. Dann als getrennten Jail/Filter bauen, nicht in den
`auth_failed`-Filter mischen.

## Schema-Migrationen

Das Schema ist aktuell per `CREATE TABLE IF NOT EXISTS` idempotent.
Sobald eine echte Schemaänderung nötig wird, braucht Bramble eine
Migrationsstrategie mit Versionsstand in der DB, bevor die Änderung
ausgerollt wird.
