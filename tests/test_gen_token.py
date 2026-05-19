"""Unit tests for the ``scripts/gen_token.py`` helper.

The script is not an importable package module, so it is loaded from
its file path the same way an operator would run it.
"""

from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gen_token.py"
_spec = importlib.util.spec_from_file_location("gen_token", _SCRIPT)
assert _spec is not None and _spec.loader is not None
gen_token = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_token)


# ---------------------------------------------------------------------------
# Token-file path resolution
# ---------------------------------------------------------------------------
class TestResolveTokensFile:
    def test_cli_wins_over_env(self) -> None:
        resolved = gen_token.resolve_tokens_file(
            Path("/cli/tokens.json"), env={"BRAMBLE_TOKENS_FILE": "/env/tokens.json"}
        )
        assert resolved == Path("/cli/tokens.json")

    def test_env_wins_over_default(self) -> None:
        resolved = gen_token.resolve_tokens_file(
            None, env={"BRAMBLE_TOKENS_FILE": "/env/tokens.json"}
        )
        assert resolved == Path("/env/tokens.json")

    def test_falls_back_to_default(self) -> None:
        assert gen_token.resolve_tokens_file(None, env={}) == gen_token.DEFAULT_TOKENS_FILE


# ---------------------------------------------------------------------------
# load_tokens()
# ---------------------------------------------------------------------------
class TestLoadTokens:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        assert gen_token.load_tokens(tmp_path / "absent.json") == {}

    def test_bad_json_raises_systemexit(self, tmp_path: Path) -> None:
        bad = tmp_path / "tokens.json"
        bad.write_text("{broken", encoding="utf-8")
        with pytest.raises(SystemExit):
            gen_token.load_tokens(bad)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
class TestMain:
    def test_creates_new_token_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "secrets" / "tokens.json"
        rc = gen_token.main(["bramble", "--tokens-file", str(path)])
        assert rc == 0
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"bramble"}
        assert len(data["bramble"]) >= 40

        out = capsys.readouterr().out
        assert "created" in out
        assert data["bramble"] in out

    def test_token_file_is_owner_only(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets" / "tokens.json"
        gen_token.main(["bramble", "--tokens-file", str(path)])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_rotating_keeps_other_projects(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "secrets" / "tokens.json"
        gen_token.main(["bramble", "--tokens-file", str(path)])
        first = json.loads(path.read_text(encoding="utf-8"))["bramble"]

        gen_token.main(["elder-berry", "--tokens-file", str(path)])
        rc = gen_token.main(["bramble", "--tokens-file", str(path)])
        assert rc == 0

        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"bramble", "elder-berry"}
        assert data["bramble"] != first  # rotated
        assert "rotated" in capsys.readouterr().out

    def test_rejects_non_kebab_project(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "secrets" / "tokens.json"
        rc = gen_token.main(["Bad_Name", "--tokens-file", str(path)])
        assert rc == 2
        assert not path.exists()
        assert "kebab-case" in capsys.readouterr().err
