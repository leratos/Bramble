# Bramble anbinden – Token erzeugen und Connector einrichten

Diese Anleitung zeigt Schritt für Schritt, wie du Bramble als MCP-Journal in
**Claude Code (VS Code)**, **Claude Desktop** und **claude.ai (Web/Mobile)**
verbindest:

1. Wo du ein Projekt-Token erzeugst (Dashboard / Admin-UI).
2. Wie du das Token für **Claude Code** und **Desktop** hinterlegst
   (statisches Bearer-Token, Abschnitte 2–3).
3. Wie du **claude.ai Web/Mobile** per **OAuth** (read-only) anbindest –
   Server aktivieren, Owner-Login anlegen, Connector einrichten (Abschnitt 4).

Die rein technische Referenz (alle Tools, Arbeitsregeln) steht in
[`ai-client-setup.md`](ai-client-setup.md). Diese Datei ist die praktische
Einrichtungsanleitung.

---

## 0. Was du brauchst

| Wert | Bramble |
| --- | --- |
| MCP-Endpunkt (HTTP) | `https://journal.last-strawberry.com/mcp/` |
| Auth-Header | `Authorization: Bearer <projekt-token>` |
| Projekt (für dieses Repo) | `bramble` |

Jedes Projekt hat ein eigenes Token. Lesen/Suchen ist projektübergreifend,
**Schreiben** ist an das Projekt des Tokens gebunden. Ein `bramble`-Token darf
also nur in `project="bramble"` schreiben.

> ⚠️ **Token sind Geheimnisse.** Niemals ins Repo committen, nicht in Chats
> oder Logs einfügen. Bramble zeigt ein Token nur **einmal** bei der Erzeugung.

---

## 1. Token erzeugen (Dashboard)

Es gibt zwei Wege. Der bevorzugte Weg ist die **Admin-UI** ("Dashboard"); der
CLI-Weg ist der Fallback ohne UI.

### Weg A – Admin-UI über SSH-Tunnel (empfohlen)

Die Admin-UI ist aus Sicherheitsgründen **nicht** öffentlich erreichbar. Sie
bindet nur an `127.0.0.1:8770` auf dem Server und wird per SSH-Tunnel geöffnet.

1. SSH-Tunnel von deinem Rechner aufbauen:

   ```sh
   ssh -L 8770:127.0.0.1:8770 lera@h2724315.stratoserver.net
   ```

2. Im Browser lokal öffnen:

   ```text
   http://127.0.0.1:8770
   ```

3. Mit dem Admin-Passwort einloggen und zu **Tokens** (`/tokens`) gehen.

4. Token-Aktion ausführen:
   - **Neu erzeugen:** Projektnamen eintragen (kebab-case, z. B. `bramble`)
     → *Token erstellen*. Der neue Token wird **genau einmal** in der Antwort
     angezeigt – sofort kopieren.
   - **Rotieren:** Projekt in der Liste wählen → *Rotieren*. Der neue Token
     wird einmalig angezeigt; der alte wird ungültig.
   - **Sperren:** Projekt wählen → *Entfernen*.

5. **Wichtig – Service neu starten.** Der MCP-Server liest die Token-Datei nur
   beim Start. Nach jedem Erzeugen/Rotieren/Entfernen auf dem Server:

   ```sh
   systemctl restart bramble
   ```

   (Die Admin-UI zeigt den genauen Restart-Befehl ebenfalls an.)

### Weg B – CLI auf dem Server (Fallback)

Falls die Admin-UI nicht läuft, direkt auf dem Host:

```sh
sudo -u bramble BRAMBLE_TOKENS_FILE=/opt/bramble/secrets/tokens.json \
    /opt/bramble/.venv/bin/python /opt/bramble/scripts/gen_token.py bramble
systemctl restart bramble
```

Das Skript gibt das Token **einmalig** auf stdout aus. Ein erneuter Aufruf für
ein bestehendes Projekt **rotiert** dessen Token.

> Token leben nur in `/opt/bramble/secrets/tokens.json` (Modus `600`). Sie
> stehen nie im Repo.

Halte den kopierten Token-Wert bereit – ihn trägst du jetzt in die Clients ein.

---

## 2. Claude Code (VS Code) einbinden

Claude Code unterstützt statische Bearer-Token für Remote-MCP-Server voll.
Für ein lokales Single-User-Setup ist der einfachste und sicherste Weg der
Befehl mit `--scope local` (Variante A). Eine geteilte `.mcp.json` im Repo
(Variante B) brauchst du nur, wenn ein **Team** denselben Connector per Git
bekommen soll.

### Variante A – per Befehl mit `--scope local` (empfohlen)

Im Terminal (im Projektordner) ausführen:

```sh
claude mcp add --transport http --scope local bramble \
    https://journal.last-strawberry.com/mcp/ \
    --header "Authorization: Bearer DEIN_TOKEN_HIER"
```

Damit ist alles erledigt: Der Token wird in deiner persönlichen
`~/.claude.json` gespeichert – **nicht** im Repo – und Claude Code verbindet
sich ab sofort in jeder Session automatisch. Keine Umgebungsvariable, kein
manuelles JSON nötig.

**Scope wählen** (`--scope`):

| Scope | Speicherort | Wann verwenden |
| --- | --- | --- |
| `local` (Default) | `~/.claude.json` (pro Projekt) | **Standard** – nur du, nur dieses Projekt |
| `user` | `~/.claude.json` (global) | wenn du Bramble in **allen** deinen Projekten willst |
| `project` | `.mcp.json` im Repo-Root | nur fürs **Team** (siehe Variante B) |

> Sowohl `local` als auch `user` legen den Token in `~/.claude.json` ab, also
> außerhalb des Repos. Beide sind sicher; `user` ist nur global statt
> projektgebunden.

### Variante B – geteilte `.mcp.json` fürs Team

Nur nötig, wenn der Connector per Git an mehrere Personen verteilt werden soll.
Dann darf der Token **nicht** in der Datei stehen – stattdessen per
Umgebungsvariable einsetzen:

```json
{
  "mcpServers": {
    "bramble": {
      "type": "http",
      "url": "https://journal.last-strawberry.com/mcp/",
      "headers": {
        "Authorization": "Bearer ${BRAMBLE_TOKEN}"
      }
    }
  }
}
```

Jede Person setzt das Token einmal dauerhaft im eigenen Benutzerprofil
(Windows PowerShell):

```powershell
[Environment]::SetEnvironmentVariable("BRAMBLE_TOKEN", "dein-token", "User")
```

Danach das Terminal (bzw. VS Code) einmal neu starten, damit die Variable
übernommen wird. Für einen einmaligen Test reicht auch die Session-Variante
`$env:BRAMBLE_TOKEN = "dein-token"` (gilt nur bis zum Schließen des Terminals).

> ⚠️ Eine dauerhafte `User`-Umgebungsvariable liegt im Klartext in der
> Windows-Registry und ist für jeden Prozess unter deinem Benutzerkonto
> lesbar – sicherheitstechnisch dieselbe Exposition wie der Token in
> `~/.claude.json`. Für ein lokales Setup also kein Nachteil, aber auch kein
> Mehrwert: Wenn du nicht im Team teilst, nimm einfach Variante A.

### Verbindung prüfen

```sh
claude mcp list           # zeigt 'bramble' und Verbindungsstatus
claude mcp get bramble    # Details zum Server
```

In einer laufenden Session:

```text
/mcp
```

Erwartung: `bramble` ist verbunden und die zehn Journal-Tools (`journal_guide`,
`journal_read`, `journal_append`, …) sind sichtbar.

> In der VS-Code-Extension nutzt Claude Code dieselbe Konfiguration wie die
> CLI. Wenn `claude mcp list` den Server zeigt, sieht ihn auch die Extension.

---

## 3. Claude Desktop einbinden (statisches Token)

> **claude.ai (Web/Mobile):** Die Custom-Connector-Oberfläche unterstützt
> **kein** statisches `Authorization: Bearer`-Token – nur OAuth. Dafür gibt es
> seit Phase 6 den **self-hosted OAuth-Weg**: siehe **Abschnitt 4**. Dieser
> Abschnitt 3 beschreibt den einfachen statischen Token-Weg für **Claude
> Desktop**.

Der statische Weg für die Claude-**Desktop**-App läuft über ihre
Konfigurationsdatei.

### Claude Desktop – `claude_desktop_config.json`

1. Konfigurationsdatei öffnen (bei Bedarf anlegen):

   | Plattform | Pfad |
   | --- | --- |
   | Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
   | macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
   | Linux | `~/.config/Claude/claude_desktop_config.json` |

2. Den Bramble-Server unter `mcpServers` eintragen:

   ```json
   {
     "mcpServers": {
       "bramble": {
         "type": "http",
         "url": "https://journal.last-strawberry.com/mcp/",
         "headers": {
           "Authorization": "Bearer DEIN_TOKEN_HIER"
         }
       }
     }
   }
   ```

3. **Claude Desktop neu starten**, damit die Konfiguration geladen wird.

4. Prüfen: Im Chat das MCP-/Tools-Menü öffnen – `bramble` muss als verbundener
   Server mit seinen Journal-Tools erscheinen.

> Falls deine Desktop-Version den nativen `type: "http"`-Eintrag (noch) nicht
> akzeptiert, gibt es den Brücken-Weg über `mcp-remote`:
>
> ```json
> {
>   "mcpServers": {
>     "bramble": {
>       "command": "npx",
>       "args": [
>         "-y", "mcp-remote",
>         "https://journal.last-strawberry.com/mcp/",
>         "--header", "Authorization: Bearer DEIN_TOKEN_HIER"
>       ]
>     }
>   }
> }
> ```
>
> Dafür muss Node.js/`npx` lokal installiert sein.

> ⚠️ `claude_desktop_config.json` speichert den Token im Klartext. Datei
> entsprechend schützen und den Token nicht weitergeben.

---

## 4. claude.ai Web & Mobile per OAuth (privater Connector)

Claude Web/Mobile sprechen nur **OAuth**, kein statisches Bearer-Token. Bramble
bringt dafür einen **self-hosted OAuth-2.1-Authorization-Server** mit. Standard
ist **Lesen** (`journal:read`, projektübergreifend). **Schreiben** ist optional:
schaltest du es ein, wählst du beim Verbinden auf der Zustimmungsseite **ein
Projekt**, in das dieser Connector schreiben darf – Lesen bleibt überall, das
Schreiben ist auf genau dieses eine Projekt beschränkt. Der statische Bearer-Weg
(Abschnitte 2/3) bleibt parallel nutzbar.

> ⚠️ **Voraussetzungen.** (1) Custom-Connectors in claude.ai setzen einen
> Claude-Plan voraus, der das Anlegen erlaubt (i. d. R. Team/Enterprise; das
> Feature muss für deinen Account verfügbar sein). OAuth macht Bramble
> *technisch* anbindbar – die Plan-Freigabe ist davon unabhängig. (2) Für deine
> **private** Instanz (deine Daten, dein Host) gibt es keine Compliance-Hürde –
> du schaltest OAuth + Schreiben einfach ein. Eine spätere **Arbeits**-
> Installation ist eine **separate** Instanz auf dem Arbeitsserver; dort gelten
> die IT-/DSGVO-Vorgaben deines Arbeitgebers.

### 4.1 Server: OAuth aktivieren (einmalig, auf dem Host)

Alle Schritte als Operator auf `journal.last-strawberry.com`.

1. **Owner-Login anlegen** – das ist der „Account“, mit dem **du** beim
   Verbinden die Freigabe erteilst (getrennt vom Admin-UI-Passwort). Argon2id-
   Secret in eine eigene Datei schreiben:

   ```sh
   sudo -u bramble /opt/bramble/.venv/bin/python \
       /opt/bramble/scripts/gen_admin_secret.py \
       --output /opt/bramble/secrets/oauth-owner.json --username owner
   ```

   Das Skript fragt nach einem Passwort – merke es dir, du gibst es beim
   Verbinden ein. Die Datei liegt mit Modus `600` beim `bramble`-User, nie im
   Repo.

2. **Env setzen** (in `bramble.service` bzw. dem geladenen Env-File):

   ```ini
   BRAMBLE_ENABLE_OAUTH=true
   BRAMBLE_OAUTH_PUBLIC_BASE_URL=https://journal.last-strawberry.com
   BRAMBLE_OAUTH_OWNER_SECRET_FILE=/opt/bramble/secrets/oauth-owner.json
   # Schreiben aktivieren (Projekt wählst du dann beim Consent):
   BRAMBLE_OAUTH_ALLOW_WRITE=true
   # optional: BRAMBLE_OAUTH_DB_PATH=/opt/bramble/data/oauth.db
   ```

   Fehlt das Owner-Secret, startet der Server im OAuth-Modus bewusst **nicht**.
   Ohne `BRAMBLE_OAUTH_ALLOW_WRITE=true` bleibt der OAuth-Zugang read-only.

3. **nginx** (Plesk → *Additional nginx directives*) um die OAuth-Pfade
   ergänzen – die fertigen Blöcke stehen in
   [`deploy/plesk-nginx-directives.conf`](../deploy/plesk-nginx-directives.conf):
   die beiden `/.well-known/oauth-*`, der exakte `location = /mcp` und
   `/authorize` `/oauth/login` `/oauth/consent` `/token` `/register` `/revoke`.
   Danach nginx neu laden.

4. **Service neu starten:**

   ```sh
   systemctl restart bramble
   ```

5. **Verifizieren** (von deinem Rechner):

   ```sh
   curl https://journal.last-strawberry.com/.well-known/oauth-authorization-server
   curl -i https://journal.last-strawberry.com/mcp   # erwartet: 401 + WWW-Authenticate
   ```

   Das erste liefert JSON (u. a. `code_challenge_methods_supported: ["S256"]`),
   das zweite `401` mit einem `WWW-Authenticate`-Header, der auf
   `/.well-known/oauth-protected-resource/mcp` zeigt. Damit ist die Discovery
   bereit.

### 4.2 Connector in claude.ai (Web) anlegen

Connectors lassen sich **nur** in Web/Desktop anlegen, nicht auf dem Handy.

1. In claude.ai: **Einstellungen → Connectors → Custom Connector hinzufügen**.
2. **Name:** `Bramble`. **Server-URL:**
   `https://journal.last-strawberry.com/mcp` (ohne Slash am Ende).
3. Speichern. Claude registriert sich automatisch (Dynamic Client Registration)
   und startet den OAuth-Flow – es öffnet sich ein Browser-Fenster zu Bramble.

### 4.3 Anmelden und zustimmen (dein Login)

1. Bramble zeigt eine **Anmeldeseite**: Benutzername (`owner`) + das Passwort
   aus Schritt 4.1.1 eingeben.
2. Danach die **Zustimmungsseite**: sie zeigt den anfragenden Client und die
   Redirect-URL. Wenn Schreiben aktiviert ist (4.1.2), gibt es ein Feld
   **„Schreibzugriff auf Projekt"** – trage das Projekt ein, in das dieser
   Connector schreiben darf (z. B. `bramble`), oder lass es **leer** für nur
   lesen. Lesen bleibt in jedem Fall projektübergreifend. Dann auf **Erlauben**.
3. Claude tauscht den Code gegen ein Token und meldet den Connector als
   verbunden. Die Bramble-Journal-Tools erscheinen; Schreiben geht (falls
   gewählt) genau in das angegebene Projekt.

> Ohne erfolgreichen Login **und** ausdrückliches *Erlauben* wird **kein**
> Token ausgestellt – ein bloßes Erreichen von `/authorize` genügt nicht. Die
> Schreib-Freigabe gilt pro Connector; bei einer neuen Verbindung wählst du das
> Projekt erneut.

### 4.4 Claude Mobile (Android/iOS)

Der Connector muss zuerst auf **Web** angelegt sein (Schritt 4.2). Auf dem
Handy dann nur mit **demselben Claude-Account** einloggen – der Bramble-
Connector ist vorhanden. Fragt die App nach Autorisierung, durchläufst du
denselben Login-und-Erlauben-Schritt im mobilen Browser.

### 4.5 Wenn etwas klemmt

| Symptom | Ursache / Lösung |
| --- | --- |
| Endlose 401-/Reconnect-Schleife | Im Claude-Client die Connector-Tokens löschen und neu verbinden; Claude registriert sich dann frisch. |
| `/.well-known/…` liefert 404 | nginx-Blöcke aus Schritt 4.1.3 fehlen oder nicht neu geladen. |
| Anmeldeseite lehnt ab | Falsches Owner-Passwort, oder Service nach Anlegen von `oauth-owner.json` nicht neu gestartet. |
| Connect bricht nach „Erlauben“ ab | `BRAMBLE_OAUTH_PUBLIC_BASE_URL` muss exakt die öffentliche https-URL sein. |
| Schreiben schlägt fehl | Ist `BRAMBLE_OAUTH_ALLOW_WRITE=true` gesetzt **und** hast du beim Consent ein Projekt eingetragen? Schreiben geht nur in genau dieses Projekt; in ein anderes wird abgelehnt. Ohne Projektwahl ist der Connector read-only. |
| Wiederholte Fehl-Logins | Der Owner-Login ist rate-limited; kurz warten. Bei IP-Bann siehe Fail2Ban. |

---

## 5. Erfolg verifizieren

Egal welcher Client – nach dem Verbinden kurz gegenprüfen:

1. **Tool-Liste:** Alle zehn Bramble-Tools sind sichtbar
   (`journal_guide`, `journal_read`, `journal_append`, `journal_search`,
   `journal_search_all`, `journal_context`, `journal_digest`,
   `journal_open_items`, `journal_resolve`, `journal_list_projects`).
2. **Lesen testen:** `journal_read(project="bramble", n=5)`.
3. **Konventionen:** `journal_guide()` liefert die Arbeitsregeln.
4. **Schreiben testen:** Erst als echten Eintrag, z. B.
   `journal_append(project="bramble", status="notiz", title="Client verbunden", ...)`.

Wenn das Schreiben fehlschlägt, in dieser Reihenfolge prüfen:

- Ist der `Authorization`-Header gesetzt?
- Gehört der Token wirklich zum Projekt `bramble`?
- Schreibt der Client tatsächlich nach `project="bramble"`?
- Wurde nach einer Token-Änderung `systemctl restart bramble` ausgeführt?
- Blockt evtl. Fail2Ban die IP nach mehreren Fehlversuchen?

---

## 6. Token wechseln oder sperren (statischer Pfad)

- **Rotieren:** In der Admin-UI (`/tokens`) *Rotieren* – danach den neuen Token
  in allen Clients ersetzen und auf dem Server `systemctl restart bramble`.
- **Sperren:** In der Admin-UI *Entfernen* – danach `systemctl restart bramble`.
- Nach jedem Token-Wechsel den Wert in Claude Code (`claude mcp add` erneut
  bzw. `.mcp.json`/`~/.claude.json`) **und** in `claude_desktop_config.json`
  aktualisieren.

---

## Kurz-Spickzettel

| Schritt | Befehl / Ort |
| --- | --- |
| Token erzeugen (UI) | SSH-Tunnel → `http://127.0.0.1:8770` → `/tokens` |
| Token erzeugen (CLI) | `scripts/gen_token.py bramble` + `systemctl restart bramble` |
| Claude Code anbinden | `claude mcp add --transport http --scope local bramble <url> --header "Authorization: Bearer …"` |
| Claude Desktop anbinden | `claude_desktop_config.json` → `mcpServers.bramble` (type http + headers) |
| OAuth aktivieren (Server) | Owner-Secret via `gen_admin_secret.py --output …/oauth-owner.json`, `BRAMBLE_ENABLE_OAUTH=true` (+ `BRAMBLE_OAUTH_ALLOW_WRITE=true` für Schreiben), nginx-Pfade, `systemctl restart bramble` (Abschnitt 4.1) |
| claude.ai (Web/Mobile) | Custom Connector, URL `…/mcp` → DCR + Owner-Login + *Erlauben*; Schreibprojekt beim Consent wählen (Abschnitt 4) |
| Verbindung prüfen | `claude mcp list` / `/mcp` |
