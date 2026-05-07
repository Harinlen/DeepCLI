"""User-level filesystem locations for DeepCLI."""

from __future__ import annotations

import os
from pathlib import Path

DEEPCLI_HOME_DIRNAME = ".deepcli"


def deepcli_home() -> Path:
    override = os.environ.get("DEEPCLI_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / DEEPCLI_HOME_DIRNAME


def user_home() -> Path:
    return deepcli_home()


def user_config_dir() -> Path:
    override = os.environ.get("DEEPCLI_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return user_home() / "config"


def user_state_dir() -> Path:
    override = os.environ.get("DEEPCLI_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return user_home() / "state"


def user_data_dir() -> Path:
    override = os.environ.get("DEEPCLI_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return user_home() / "data"


def user_path(*parts: str) -> Path:
    return user_home().joinpath(*parts)
