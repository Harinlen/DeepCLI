"""SecretStore migration registry."""

from __future__ import annotations

import importlib

from kernel.core.storage.migrations import Migration

SCHEMA_VERSION = 2

_baseline = importlib.import_module("kernel.core.storage.migrations.secrets.0001_baseline")
_migration_sources = importlib.import_module(
    "kernel.core.storage.migrations.secrets.0002_migration_sources"
)

MIGRATIONS: tuple[Migration, ...] = (
    (1, "baseline secrets", _baseline.migrate),
    (2, "secret migration source markers", _migration_sources.migrate),
)
