"""Harvey resolves its files the same way from every directory.

The install is meant to be used from anywhere — `harvey status` typed in a
random folder must reach the same database as one typed in the checkout. These
tests pin the resolution order, and in particular the rule that an existing
checkout database is never orphaned by the move to a per-user home.
"""

import importlib

import pytest

from harvey import paths


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway HARVEY_HOME, with no checkout database in the way."""
    target = tmp_path / "home"
    monkeypatch.setenv(paths.ENV_HOME, str(target))
    monkeypatch.delenv(paths.ENV_DB, raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "checkout")
    return target


def test_home_defaults_under_the_user_profile(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.home() == (tmp_path / ".harvey").resolve()


def test_harvey_home_env_var_wins(home):
    assert paths.home() == home.resolve()


def test_database_lives_in_the_home_for_a_normal_install(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert paths.state_db() == home.resolve() / "data" / "harvey.db"


def test_database_is_the_same_from_any_directory(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from_here = paths.state_db()
    elsewhere = tmp_path / "somewhere" / "deep"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    assert paths.state_db() == from_here


def test_existing_checkout_database_is_never_orphaned(home, tmp_path, monkeypatch):
    """Upgrading an old install must not look like losing every prospect."""
    checkout = tmp_path / "checkout"
    (checkout / "data").mkdir(parents=True)
    legacy = checkout / "data" / "harvey.db"
    legacy.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert paths.state_db() == legacy
    legacy.unlink()
    assert paths.state_db() == home.resolve() / "data" / "harvey.db"


def test_harvey_db_override_beats_everything(home, tmp_path, monkeypatch):
    forced = tmp_path / "forced.db"
    monkeypatch.setenv(paths.ENV_DB, str(forced))
    assert paths.state_db() == forced.resolve()


def test_config_prefers_cwd_then_home_then_checkout(home, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    (checkout / "harvey.yaml").write_text("shipped", encoding="utf-8")
    found = [p for p in paths.config_candidates() if p.is_file()]
    assert found[0] == checkout / "harvey.yaml"

    (home / "harvey.yaml").write_text("mine", encoding="utf-8")
    found = [p for p in paths.config_candidates() if p.is_file()]
    assert found[0] == home / "harvey.yaml"

    (work / "harvey.yaml").write_text("project", encoding="utf-8")
    found = [p for p in paths.config_candidates() if p.is_file()]
    assert found[0] == work / "harvey.yaml"


def test_local_yaml_wins_over_plain_yaml_at_the_same_level(home, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    (work / "harvey.yaml").write_text("template", encoding="utf-8")
    (work / "harvey.local.yaml").write_text("trained", encoding="utf-8")
    found = [p for p in paths.config_candidates() if p.is_file()]
    assert found[0] == work / "harvey.local.yaml"


def test_env_files_layer_home_then_checkout_then_cwd(home, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    (home / ".env").write_text("A=1", encoding="utf-8")
    (checkout / ".env").write_text("A=2", encoding="utf-8")
    (work / ".env").write_text("A=3", encoding="utf-8")
    # Least specific first: a later load_dotenv(override=True) wins.
    assert paths.env_files() == [home / ".env", checkout / ".env", work / ".env"]


def test_knowledge_falls_back_to_the_shipped_copy(home, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / "prompts").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert paths.knowledge_dir("prompts") == checkout / "prompts"

    (work / "prompts").mkdir()
    assert paths.knowledge_dir("prompts") == work / "prompts"


def test_state_module_binds_the_resolved_database(home, monkeypatch):
    """state.DB_PATH is resolved at import, so a fresh import follows the env."""
    import harvey.state as state_module

    reloaded = importlib.reload(state_module)
    try:
        assert reloaded.DB_PATH == paths.state_db()
    finally:
        monkeypatch.undo()
        importlib.reload(state_module)


def test_describe_reports_every_resolved_location(home):
    described = paths.describe()
    assert set(described) == {
        "project_root", "home", "data_dir", "database",
        "env_file", "config", "prompts", "skills",
    }
    assert described["home"] == str(home.resolve())
