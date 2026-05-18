"""ResourceStore migration registry."""

from __future__ import annotations

import importlib

from kernel.core.storage.migrations import Migration

SCHEMA_VERSION = 6

_baseline = importlib.import_module("kernel.core.storage.migrations.resource.0001_baseline")
_config_flags = importlib.import_module("kernel.core.storage.migrations.resource.0002_config_flags")
_migration_sources = importlib.import_module(
    "kernel.core.storage.migrations.resource.0003_migration_sources"
)
_agent_manager = importlib.import_module(
    "kernel.core.storage.migrations.resource.0004_agent_manager"
)
_access_router = importlib.import_module(
    "kernel.core.storage.migrations.resource.0005_access_router"
)
_schedule = importlib.import_module("kernel.core.storage.migrations.resource.0006_schedule")

MIGRATIONS: tuple[Migration, ...] = (
    (1, "baseline generic resources", _baseline.migrate),
    (2, "config and flag sections", _config_flags.migrate),
    (3, "migration source markers", _migration_sources.migrate),
    (4, "agent manager tables", _agent_manager.migrate),
    (5, "access router adapter tables", _access_router.migrate),
    (6, "scheduled task tables", _schedule.migrate),
)
