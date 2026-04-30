"""Tests for ``bccr_mcp_server.config``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bccr_mcp_server.config import Settings, load_settings
from bccr_mcp_server.errors import ConfigurationError


# --- Python idiom: autouse fixture ------------------------------------------
# A fixture marked ``autouse=True`` runs for every test in this module
# without having to be declared as a parameter. We use it here to put each
# test in a temporary, empty directory so that ``load_dotenv()`` inside
# ``load_settings()`` does NOT pick up the developer's real ``.env`` file at
# the project root. Without this guard, deleting BCCR_TOKEN from the env
# would still see it repopulated by dotenv.
@pytest.fixture(autouse=True)
def _isolate_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)


def test_loads_when_token_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCCR_TOKEN", "deadbeef")
    monkeypatch.delenv("BCCR_BASE_URL", raising=False)

    settings = load_settings()

    assert isinstance(settings, Settings)
    assert settings.bccr_token == "deadbeef"
    assert settings.bccr_base_url.startswith("https://apim.bccr.fi.cr")


def test_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BCCR_TOKEN", "deadbeef")
    monkeypatch.setenv("BCCR_BASE_URL", "https://example.invalid/api")

    settings = load_settings()

    assert settings.bccr_base_url == "https://example.invalid/api"


def test_raises_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BCCR_TOKEN", raising=False)

    with pytest.raises(ConfigurationError, match="BCCR_TOKEN"):
        load_settings()


def test_raises_when_token_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Whitespace-only values should be treated as missing — the strip() in
    # ``load_settings`` catches copy/paste accidents.
    monkeypatch.setenv("BCCR_TOKEN", "   ")

    with pytest.raises(ConfigurationError):
        load_settings()
