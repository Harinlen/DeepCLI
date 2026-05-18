"""UUID-backed SecretManager facade over SecretStore."""

from __future__ import annotations

import re
import uuid
import builtins
from dataclasses import dataclass
from pathlib import Path

from kernel.core.storage import SecretStore
from kernel.core.storage.models import SecretAuditEvent, SecretRecord

_SECRET_REF_RE = re.compile(
    r"^secret:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True, slots=True)
class SecretRef:
    """Stable config reference for one secret."""

    secret_id: str
    ref: str
    name: str
    revision: int


class SecretManager:
    """Secret metadata and UUID reference facade.

    This facade deliberately stores opaque bytes in SecretStore; encryption/key
    material remains outside Slice 3. Config references use ``secret:<uuid>``
    and never the mutable display name.
    """

    def __init__(self, *, home: Path) -> None:
        self._home = home
        self._store: SecretStore | None = None

    def startup(self) -> None:
        if self._store is None:
            self._store = SecretStore.open(self._home)

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def create(self, name: str, value: bytes, actor: str | None) -> SecretRef:
        self._ensure_unique_name(name)
        secret_id = str(uuid.uuid4())
        record = self._store_required().cas_secret(
            secret_id=secret_id,
            name=name,
            encrypted_payload=value,
            expected_revision=None,
            actor=actor,
        )
        return _ref_from_record(record)

    def list(self) -> builtins.list[SecretRecord]:
        return self._store_required().list_secrets()

    def resolve(self, secret_id: str) -> bytes:
        value = self._store_required().get_ciphertext(secret_id)
        if value is None:
            raise KeyError(f"Secret not found: {secret_id}")
        self._store_required().append_audit(secret_id, "secret.resolve", None, {})
        return value

    def rename(
        self,
        secret_id: str,
        name: str,
        *,
        expected_revision: int,
        actor: str | None,
    ) -> SecretRef:
        self._ensure_unique_name(name, excluding_secret_id=secret_id)
        current_value = self._store_required().get_ciphertext(secret_id)
        if current_value is None:
            raise KeyError(f"Secret not found: {secret_id}")
        record = self._store_required().cas_secret(
            secret_id=secret_id,
            name=name,
            encrypted_payload=current_value,
            expected_revision=expected_revision,
            actor=actor,
        )
        self._store_required().append_audit(
            secret_id,
            "secret.rename",
            actor,
            {"name": name, "revision": record.revision},
        )
        return _ref_from_record(record)

    def delete(
        self,
        secret_id: str,
        *,
        expected_revision: int,
        actor: str | None,
        confirm: bool,
    ) -> bool:
        if not confirm:
            raise ValueError("Secret delete requires confirm=True")
        return self._store_required().delete_secret(
            secret_id,
            expected_revision=expected_revision,
            actor=actor,
        )

    def audit(self, secret_id: str | None = None) -> builtins.list[SecretAuditEvent]:
        return self._store_required().audit_events(secret_id)

    @staticmethod
    def validate_config_ref(ref: str) -> str:
        if not _SECRET_REF_RE.match(ref):
            raise ValueError("Secret config references must use secret:<uuid>")
        return ref

    def _ensure_unique_name(
        self,
        name: str,
        *,
        excluding_secret_id: str | None = None,
    ) -> None:
        for record in self._store_required().list_secrets():
            if record.name == name and record.secret_id != excluding_secret_id:
                raise ValueError(f"Secret name already exists: {name}")

    def _store_required(self) -> SecretStore:
        if self._store is None:
            raise RuntimeError("SecretManager.startup() not called")
        return self._store


def _ref_from_record(record: SecretRecord) -> SecretRef:
    return SecretRef(
        secret_id=record.secret_id,
        ref=f"secret:{record.secret_id}",
        name=record.name,
        revision=record.revision,
    )
