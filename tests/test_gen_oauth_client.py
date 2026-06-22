"""Tests for scripts/gen_oauth_client.py."""

from __future__ import annotations

import importlib.util
import os
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


def _secret_from_file(path: Path) -> str:
    prefix = f"{ENV_OAUTH_STATIC_CLIENT_SECRET}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise AssertionError("no secret line in env file")


class TestGenOAuthClient:
    def test_requires_write_so_secret_is_not_printed(
        self, gen, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Without --write the script refuses rather than echo the secret.
        rc = gen.main(["--redirect-uri", _CB, "--public-base-url", _BASE])
        assert rc == 2
        captured = capsys.readouterr()
        assert ENV_OAUTH_STATIC_CLIENT_SECRET not in captured.out

    def test_write_creates_file_with_env_block(self, gen, tmp_path: Path) -> None:
        target = tmp_path / "secrets" / "oauth.env"
        rc = gen.main(
            ["--redirect-uri", _CB, "--public-base-url", _BASE, "--write", str(target)]
        )
        assert rc == 0
        content = target.read_text(encoding="utf-8")
        assert f"{ENV_OAUTH_STATIC_CLIENT_ID}=" in content
        assert f"{ENV_OAUTH_STATIC_CLIENT_SECRET}=" in content
        assert f"{ENV_OAUTH_STATIC_CLIENT_REDIRECT_URIS}={_CB}" in content

    def test_secret_is_never_written_to_stdout_or_stderr(
        self, gen, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "oauth.env"
        rc = gen.main(
            ["--redirect-uri", _CB, "--public-base-url", _BASE, "--write", str(target)]
        )
        assert rc == 0
        secret = _secret_from_file(target)
        captured = capsys.readouterr()
        assert secret not in captured.out
        assert secret not in captured.err

    def test_custom_client_id_respected(
        self, gen, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "oauth.env"
        gen.main(
            [
                "--redirect-uri",
                _CB,
                "--public-base-url",
                _BASE,
                "--client-id",
                "my-id",
                "--write",
                str(target),
            ]
        )
        assert f"{ENV_OAUTH_STATIC_CLIENT_ID}=my-id" in target.read_text(
            encoding="utf-8"
        )
        assert "my-id" in capsys.readouterr().out  # client_id is not secret

    def test_rejects_plain_http_redirect(self, gen, tmp_path: Path) -> None:
        rc = gen.main(
            [
                "--redirect-uri",
                "http://claude.ai/cb",
                "--public-base-url",
                _BASE,
                "--write",
                str(tmp_path / "oauth.env"),
            ]
        )
        assert rc == 2

    def test_requires_public_base_url(
        self, gen, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv(ENV_OAUTH_PUBLIC_BASE_URL, raising=False)
        rc = gen.main(["--redirect-uri", _CB, "--write", str(tmp_path / "oauth.env")])
        assert rc == 2

    def test_reads_base_url_from_env(
        self, gen, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(ENV_OAUTH_PUBLIC_BASE_URL, _BASE)
        target = tmp_path / "oauth.env"
        rc = gen.main(["--redirect-uri", _CB, "--write", str(target)])
        assert rc == 0
        assert f"{ENV_OAUTH_STATIC_CLIENT_ID}=" in target.read_text(encoding="utf-8")

    @pytest.mark.skipif(
        os.name == "nt", reason="POSIX owner-only mode bits are not reliable on Windows"
    )
    def test_write_tightens_existing_insecure_file(self, gen, tmp_path: Path) -> None:
        # An existing group/world-readable file must be tightened to 600 so the
        # fresh secret is not written into an insecure file.
        target = tmp_path / "oauth.env"
        target.write_text("stale", encoding="utf-8")
        target.chmod(0o644)
        rc = gen.main(
            ["--redirect-uri", _CB, "--public-base-url", _BASE, "--write", str(target)]
        )
        assert rc == 0
        assert (target.stat().st_mode & 0o777) == 0o600
