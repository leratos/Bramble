"""Tests for scripts/gen_oauth_client.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from bramble.oauth_config import (
    ENV_OAUTH_PUBLIC_BASE_URL,
    ENV_OAUTH_STATIC_CLIENT_ID,
    ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS,
    ENV_OAUTH_STATIC_CLIENT_SECRET,
)

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gen_oauth_client.py"
_BASE = "https://journal.last-strawberry.com"
_CB = "https://claude.ai/api/mcp/auth_callback"


def _load_module():
    spec = importlib.util.spec_from_file_location("gen_oauth_client", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gen():
    return _load_module()


class TestGenOAuthClient:
    def test_prints_env_block(self, gen, capsys: pytest.CaptureFixture[str]) -> None:
        rc = gen.main(
            ["--redirect-uri", _CB, "--public-base-url", _BASE]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert f"{ENV_OAUTH_STATIC_CLIENT_ID}=" in out
        assert f"{ENV_OAUTH_STATIC_CLIENT_SECRET}=" in out
        assert f"{ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS}={_CB}" in out

    def test_custom_client_id_respected(
        self, gen, capsys: pytest.CaptureFixture[str]
    ) -> None:
        gen.main(
            ["--redirect-uri", _CB, "--public-base-url", _BASE, "--client-id", "my-id"]
        )
        out = capsys.readouterr().out
        assert f"{ENV_OAUTH_STATIC_CLIENT_ID}=my-id" in out

    def test_rejects_plain_http_redirect(self, gen) -> None:
        rc = gen.main(
            ["--redirect-uri", "http://claude.ai/cb", "--public-base-url", _BASE]
        )
        assert rc == 2

    def test_requires_public_base_url(
        self, gen, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_OAUTH_PUBLIC_BASE_URL, raising=False)
        rc = gen.main(["--redirect-uri", _CB])
        assert rc == 2

    def test_write_creates_file(self, gen, tmp_path: Path) -> None:
        target = tmp_path / "secrets" / "oauth.env"
        rc = gen.main(
            [
                "--redirect-uri",
                _CB,
                "--public-base-url",
                _BASE,
                "--write",
                str(target),
            ]
        )
        assert rc == 0
        content = target.read_text(encoding="utf-8")
        assert f"{ENV_OAUTH_STATIC_CLIENT_SECRET}=" in content
        assert _CB in content

    def test_reads_base_url_from_env(
        self, gen, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv(ENV_OAUTH_PUBLIC_BASE_URL, _BASE)
        rc = gen.main(["--redirect-uri", _CB])
        assert rc == 0
        assert f"{ENV_OAUTH_STATIC_CLIENT_ID}=" in capsys.readouterr().out
