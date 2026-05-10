from pathlib import Path

from kernel.core.paths import (
    deepcli_home,
    user_config_dir,
    user_data_dir,
    user_home,
    user_path,
    user_state_dir,
)


def test_deepcli_home_is_new_default(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DEEPCLI_HOME", raising=False)
    monkeypatch.delenv("DEEPCLI_CONFIG_DIR", raising=False)
    monkeypatch.delenv("DEEPCLI_STATE_DIR", raising=False)
    monkeypatch.delenv("DEEPCLI_DATA_DIR", raising=False)

    assert deepcli_home() == tmp_path / ".deepcli"
    assert user_home() == tmp_path / ".deepcli"
    assert user_config_dir() == tmp_path / ".deepcli" / "config"
    assert user_state_dir() == tmp_path / ".deepcli" / "state"
    assert user_data_dir() == tmp_path / ".deepcli" / "data"
    assert user_path("prompts") == tmp_path / ".deepcli" / "prompts"


def test_legacy_home_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DEEPCLI_HOME", raising=False)

    (tmp_path / ".mustang").mkdir()
    assert user_home() == tmp_path / ".deepcli"


def test_explicit_overrides_win(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DEEPCLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPCLI_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("DEEPCLI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DEEPCLI_DATA_DIR", str(tmp_path / "data"))

    assert user_home() == tmp_path / "home"
    assert user_config_dir() == tmp_path / "cfg"
    assert user_state_dir() == tmp_path / "state"
    assert user_data_dir() == tmp_path / "data"
