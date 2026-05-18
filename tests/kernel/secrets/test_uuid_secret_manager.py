from __future__ import annotations

import pytest

from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.secrets.manager import SecretManager
from kernel.core.storage import ResourceStore


def test_create_unique_secret_name(tmp_path) -> None:
    manager = SecretManager(home=tmp_path)
    manager.startup()
    try:
        ref = manager.create("api-key", b"ciphertext", actor="primary")
        assert ref.ref == f"secret:{ref.secret_id}"
        assert manager.list()[0].name == "api-key"
    finally:
        manager.close()


def test_reject_duplicate_name(tmp_path) -> None:
    manager = SecretManager(home=tmp_path)
    manager.startup()
    try:
        manager.create("api-key", b"ciphertext", actor="primary")
        with pytest.raises(ValueError, match="already exists"):
            manager.create("api-key", b"other", actor="primary")
    finally:
        manager.close()


def test_rename_increments_revision_and_audits(tmp_path) -> None:
    manager = SecretManager(home=tmp_path)
    manager.startup()
    try:
        ref = manager.create("api-key", b"ciphertext", actor="primary")
        renamed = manager.rename(
            ref.secret_id,
            "renamed",
            expected_revision=ref.revision,
            actor="primary",
        )

        assert renamed.secret_id == ref.secret_id
        assert renamed.revision == 2
        assert renamed.name == "renamed"
        assert [event.event_type for event in manager.audit(ref.secret_id)] == [
            "secret.write",
            "secret.write",
            "secret.rename",
        ]
    finally:
        manager.close()


def test_delete_requires_confirm(tmp_path) -> None:
    manager = SecretManager(home=tmp_path)
    manager.startup()
    try:
        ref = manager.create("api-key", b"ciphertext", actor="primary")
        with pytest.raises(ValueError, match="confirm=True"):
            manager.delete(
                ref.secret_id,
                expected_revision=ref.revision,
                actor="primary",
                confirm=False,
            )
    finally:
        manager.close()


def test_config_reference_validation_accepts_uuid_and_rejects_name(tmp_path) -> None:
    manager = SecretManager(home=tmp_path)
    manager.startup()
    try:
        ref = manager.create("api-key", b"ciphertext", actor="primary")
        assert manager.validate_config_ref(ref.ref) == ref.ref
        with pytest.raises(ValueError, match="secret:<uuid>"):
            manager.validate_config_ref("secret:api-key")
    finally:
        manager.close()


def test_secret_rename_does_not_rewrite_config_reference(tmp_path) -> None:
    manager = SecretManager(home=tmp_path)
    manager.startup()
    try:
        ref = manager.create("api-key", b"ciphertext", actor="primary")
        store = ResourceStore.open(tmp_path)
        try:
            config = ConfigSQLiteBackend(store).write(
                file="config",
                section="provider",
                payload={"api_key": ref.ref},
                expected_revision=None,
                actor="primary",
            )
            manager.rename(
                ref.secret_id,
                "new-name",
                expected_revision=ref.revision,
                actor="primary",
            )
            after = ConfigSQLiteBackend(store).read(file="config", section="provider")
        finally:
            store.close()

        assert after is not None
        assert after.revision == config.revision
        assert after.payload["api_key"] == ref.ref
        assert manager.resolve(ref.secret_id) == b"ciphertext"
    finally:
        manager.close()
