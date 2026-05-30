# Beitragen zu Bramble

Danke für dein Interesse! Bramble ist ein self-hosted MCP-Server für ein
projektübergreifendes Entwicklungsjournal. Bitte lies vor einem Beitrag das
[Sicherheitsmodell](SECURITY.md) — insbesondere, dass Lesen projektübergreifend
ist und es keine Mandantentrennung gibt.

## Entwicklungsumgebung

Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

Versionierte git-Hooks aktivieren (Branch-Policy + pytest vor dem Push):

```bash
git config core.hooksPath .githooks
```

## Tests

- Runner: `pytest` aus dem Projekt-Root.
- `asyncio_mode = "auto"` (siehe `pyproject.toml`).
- Tests liegen flach in `tests/`, Namenskonvention `test_{modul}.py`.
- Jede Änderung mit Tests absichern: Happy Path, wichtigste Fehlerfälle,
  relevante Edge Cases. Mocks bevorzugt mit `unittest.mock`.
- Vor einem PR muss die volle Suite grün sein; die CI führt sie ebenfalls aus.

## Branches und Commits

- Keine direkten Commits auf `main` (der pre-commit-Hook verweigert das).
- Feature-Arbeit: `feature/kurzbeschreibung`; Bugfix: `fix/...`;
  reine Tooling-/Doku-Änderungen: `chore/...` bzw. `docs/...`.
  Branch-Namen lowercase mit Bindestrichen.
- Aussagekräftige Commit-Messages (gerne Conventional-Commits-Stil:
  `feat(...)`, `fix(...)`, `docs(...)`, `chore(...)`).

## Code-Stil

- OOP: eine Klasse pro Datei (`snake_case`-Dateiname), Kommunikation über
  klare Interfaces, Dependency Injection über den Konstruktor.
- **Append-only-Invariant**: keine `update`/`delete`-Tools oder -Pfade am
  Journal. Korrekturen sind neue Einträge.
- Linting: ruff (`line-length = 100`, Regeln `E,F,W,I,B,UP,SIM` in
  `pyproject.toml`). Bitte vor dem PR lokal `ruff check .` laufen lassen.
- Logging über `logging.getLogger(__name__)`, kein `print()` für
  Fehler/Warnungen; spezifische Exceptions fangen, kein bare `except`.
- Neue Laufzeit-Dependencies nur mit Begründung im PR.

## Pull Requests

1. Branch von `main` abzweigen, Änderung + Tests umsetzen.
2. Volle Suite grün, ruff sauber.
3. PR gegen `main` öffnen; die CI muss durchlaufen.
4. Beiträge werden unter der [Apache-2.0-Lizenz](LICENSE) angenommen.

## Hinweis für KI-Agenten

Für LLM-gestützte Beiträge gibt es zusätzlich [AGENTS.md](AGENTS.md). Der
Maintainer pflegt das laufende Projektgedächtnis im Bramble-MCP-Journal;
externe Beitragende brauchen dafür kein Token.
