"""Tests for the admin UI translation catalog."""

from __future__ import annotations

from bramble.admin_i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    make_translator,
    normalize_language,
)


def test_english_is_the_default_language() -> None:
    assert DEFAULT_LANGUAGE == "en"
    assert "en" in SUPPORTED_LANGUAGES
    assert "de" in SUPPORTED_LANGUAGES


def test_normalize_language_defaults_and_normalizes() -> None:
    assert normalize_language(None) == "en"
    assert normalize_language("DE") == "de"
    assert normalize_language(" en ") == "en"
    assert normalize_language("fr") == "en"  # unsupported -> default


def test_translator_returns_language_specific_text() -> None:
    assert make_translator("en")("nav_projects") == "Projects"
    assert make_translator("de")("nav_projects") == "Projekte"


def test_translator_returns_key_for_unknown_key() -> None:
    # A typo renders visibly rather than crashing.
    assert make_translator("de")("does_not_exist") == "does_not_exist"


def test_unsupported_language_falls_back_to_english() -> None:
    assert make_translator("fr")("nav_projects") == "Projects"
