"""Integration tests: SecretManager + ConfigManager ${secret:name} expansion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from kernel.config import ConfigManager
from kernel.secrets import SecretManager
from kernel.secrets.types import SecretNotFoundError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(data, f)


class _TestSchema(BaseModel):
    api_key: str = ""
    base_url: str = ""
    extra: dict[str, str] = {}
    items: list[str] = []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_config_resolves_secret(tmp_path: Path):
    """ConfigManager expands ${secret:name} from SecretManager."""
    # Setup SecretManager with a test secret.
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    sm.set("my-api-key", "sk-real-value")

    # Write config YAML with a secret reference.
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "test.yaml", {
        "provider": {
            "api_key": "${secret:my-api-key}",
            "base_url": "https://api.example.com",
        },
    })

    # Start ConfigManager with the secret resolver.
    cm = ConfigManager(
        global_dir=config_dir,
        project_dir=tmp_path / "project",
        secret_resolver=sm.get,
    )
    await cm.startup()

    # Bind the section — expansion happens here.
    section = cm.bind_section(file="test", section="provider", schema=_TestSchema)
    cfg = section.get()

    assert cfg.api_key == "sk-real-value"
    assert cfg.base_url == "https://api.example.com"

    sm.close()


@pytest.mark.anyio
async def test_no_resolver_passes_through(tmp_path: Path):
    """Without secret_resolver, ${secret:...} stays as literal text."""
    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "test.yaml", {
        "provider": {
            "api_key": "${secret:my-key}",
        },
    })

    cm = ConfigManager(
        global_dir=config_dir,
        project_dir=tmp_path / "project",
        secret_resolver=None,
    )
    await cm.startup()

    section = cm.bind_section(file="test", section="provider", schema=_TestSchema)
    cfg = section.get()

    # Not expanded — literal ${secret:...} preserved.
    assert cfg.api_key == "${secret:my-key}"


@pytest.mark.anyio
async def test_nested_dict_expansion(tmp_path: Path):
    """Expansion works inside nested dicts."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    sm.set("header-token", "Bearer xyz")

    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "test.yaml", {
        "provider": {
            "extra": {
                "authorization": "${secret:header-token}",
            },
        },
    })

    cm = ConfigManager(
        global_dir=config_dir,
        project_dir=tmp_path / "project",
        secret_resolver=sm.get,
    )
    await cm.startup()

    section = cm.bind_section(file="test", section="provider", schema=_TestSchema)
    assert section.get().extra["authorization"] == "Bearer xyz"

    sm.close()


@pytest.mark.anyio
async def test_list_expansion(tmp_path: Path):
    """Expansion works inside lists."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    sm.set("item1", "resolved1")

    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "test.yaml", {
        "provider": {
            "items": ["${secret:item1}", "literal"],
        },
    })

    cm = ConfigManager(
        global_dir=config_dir,
        project_dir=tmp_path / "project",
        secret_resolver=sm.get,
    )
    await cm.startup()

    section = cm.bind_section(file="test", section="provider", schema=_TestSchema)
    assert section.get().items == ["resolved1", "literal"]

    sm.close()


@pytest.mark.anyio
async def test_missing_secret_raises_on_bind(tmp_path: Path):
    """Referencing a nonexistent secret raises SecretNotFoundError at bind time."""
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    # Don't set 'missing-key'.

    config_dir = tmp_path / "config"
    _write_yaml(config_dir / "test.yaml", {
        "provider": {
            "api_key": "${secret:missing-key}",
        },
    })

    cm = ConfigManager(
        global_dir=config_dir,
        project_dir=tmp_path / "project",
        secret_resolver=sm.get,
    )
    await cm.startup()

    with pytest.raises(SecretNotFoundError, match="missing-key"):
        cm.bind_section(file="test", section="provider", schema=_TestSchema)

    sm.close()
