"""Tests for configuration loading."""

import tempfile
import os

import pytest
import yaml

from harvey.config import HarveyConfig, EnvConfig, load_env


def test_config_from_dict():
    """HarveyConfig can be constructed from a dict (YAML-like)."""
    data = {
        "persona": {
            "name": "Harvey",
            "company": "Acme",
            "role": "BDR",
            "email": "harvey@acme.com",
            "linkedin": "",
            "tone": "professional",
        },
        "product": {
            "name": "Widget",
            "description": "A great widget",
            "pricing": "$99/mo",
            "key_benefits": ["Fast", "Reliable"],
            "objection_responses": {"too expensive": "Consider the ROI"},
        },
        "icp": {
            "industries": ["SaaS"],
            "company_size": "10-200",
            "titles": ["VP of Sales"],
            "geography": ["US"],
        },
    }
    config = HarveyConfig(**data)
    assert config.persona.name == "Harvey"
    assert config.product.name == "Widget"
    assert config.icp.industries == ["SaaS"]
    assert config.channels.email.max_daily_sends == 50  # default
    assert config.usage.heartbeat_interval_minutes == 15  # default


def test_env_config_defaults():
    """EnvConfig has sensible defaults when env vars are unset."""
    env = EnvConfig()
    assert env.instantly_api_key == ""
    assert env.serper_api_key == ""


def test_offer_config_defaults():
    """OfferConfig defaults to book_call via calendar_link."""
    data = {
        "persona": {"name": "H", "company": "A", "role": "B", "email": "e", "linkedin": "", "tone": "t"},
        "product": {"name": "P", "description": "D", "pricing": "$1", "key_benefits": [], "objection_responses": {}},
        "icp": {"industries": [], "company_size": "", "titles": [], "geography": []},
    }
    config = HarveyConfig(**data)
    assert config.product.offer.goal == "book_call"
    assert config.product.offer.booking_method == "calendar_link"
    assert config.product.offer.meeting_duration == "15 minutes"


# ── Which .env the credentials actually come from ──
#
# These exist because a provider configured in ~/.harvey-score/.env reported
# itself as "not configured": load_env() read a .env beside the working
# directory and never the profile's own.


def _write_env(directory, **values):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".env").write_text(
        "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


@pytest.fixture
def env_sandbox(tmp_path, monkeypatch):
    """A profile home and a checkout, with the real environment emptied of
    the keys under test so a developer's own shell cannot mask a failure.

    os.environ is restored wholesale afterwards: load_env() populates it on
    purpose, so a test that loads a .env would otherwise leave those keys
    behind and quietly configure a provider for every test that follows.
    """
    import harvey.paths as paths_module

    home = tmp_path / "profile"
    checkout = tmp_path / "checkout"
    checkout.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HARVEY_HOME", str(home))
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", checkout)
    monkeypatch.chdir(checkout)
    for key in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "HUNTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    before = dict(os.environ)
    try:
        yield home, checkout
    finally:
        os.environ.clear()
        os.environ.update(before)


def test_credentials_in_the_profile_home_are_read(env_sandbox):
    home, _ = env_sandbox
    _write_env(home, DATAFORSEO_LOGIN="home@example.com",
               DATAFORSEO_PASSWORD="home-secret")

    env = load_env()

    assert env.dataforseo_login == "home@example.com"
    assert env.dataforseo_password == "home-secret"


def test_a_checkout_env_overrides_the_profile_home(env_sandbox):
    home, checkout = env_sandbox
    _write_env(home, DATAFORSEO_LOGIN="home@example.com")
    _write_env(checkout, DATAFORSEO_LOGIN="checkout@example.com")

    assert load_env().dataforseo_login == "checkout@example.com"


def test_an_exported_variable_beats_every_file(env_sandbox, monkeypatch):
    """A cloud environment and CI supply credentials as real environment
    variables; a stale file must not shadow them."""
    home, checkout = env_sandbox
    _write_env(home, DATAFORSEO_LOGIN="home@example.com")
    _write_env(checkout, DATAFORSEO_LOGIN="checkout@example.com")
    monkeypatch.setenv("DATAFORSEO_LOGIN", "exported@example.com")

    assert load_env().dataforseo_login == "exported@example.com"


def test_no_env_file_anywhere_is_not_an_error(env_sandbox):
    env = load_env()

    assert env.dataforseo_login == ""
    assert env.dataforseo_password == ""


def test_the_provider_reports_itself_configured_once_the_profile_env_is_read(env_sandbox):
    """The symptom that started this: 'needs DATAFORSEO_LOGIN,
    DATAFORSEO_PASSWORD in .env' while both sat in the profile's own file."""
    from harvey.collectors.discover import PROVIDERS

    home, _ = env_sandbox
    _write_env(home, DATAFORSEO_LOGIN="u", DATAFORSEO_PASSWORD="p")

    env = load_env().model_dump()

    assert PROVIDERS["dataforseo_listings"].configured(env) is True
