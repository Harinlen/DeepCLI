"""Password hashing / verification tests.

Scrypt is slow on purpose.  To keep test runtime reasonable we pass
a small hash through the serializer rather than calling
``hash_password`` many times — round-trip covers the parse path and
a single ``hash_password`` / ``verify_password`` pair covers the
real-parameters path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.connection_auth._fs import atomic_write_0600
from kernel.connection_auth.password import (
    _deserialize,
    _serialize,
    delete_hash,
    hash_password,
    load_hash,
    verify_password,
)


def test_hash_then_verify_accepts_correct_password() -> None:
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse battery staple", stored) is True


def test_hash_then_verify_rejects_wrong_password() -> None:
    stored = hash_password("correct horse battery staple")
    assert verify_password("wrong password", stored) is False


def test_hash_uses_fresh_salt_each_call() -> None:
    """Two hashes of the same plaintext must not collide."""
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    # Both must still verify.
    assert verify_password("same", a)
    assert verify_password("same", b)


def test_verify_rejects_malformed_hash() -> None:
    assert verify_password("anything", "not-a-hash") is False
    assert verify_password("anything", "scrypt$1$1$1$too-few-fields") is False
    assert verify_password("anything", "") is False


def test_verify_rejects_unknown_algorithm_tag() -> None:
    stored = hash_password("pw")
    # Swap the tag and ensure parse refuses.
    _, rest = stored.split("$", 1)
    mangled = "argon2$" + rest
    assert verify_password("pw", mangled) is False


def test_serialize_round_trip_preserves_all_fields() -> None:
    n, r, p = 2**14, 4, 2
    salt = b"sixteen_bytes_!!"
    key = b"A" * 64
    serialized = _serialize(n, r, p, salt, key)
    parsed = _deserialize(serialized)
    assert parsed == (n, r, p, salt, key)


def test_deserialize_rejects_zero_parameters() -> None:
    with pytest.raises(ValueError, match="positive"):
        _deserialize("scrypt$0$8$1$YWFh$YmJi")


def test_deserialize_rejects_bad_base64() -> None:
    with pytest.raises(ValueError, match="base64"):
        _deserialize("scrypt$16384$8$1$!!!$YmJi")


def test_load_hash_missing_returns_none(tmp_path: Path) -> None:
    assert load_hash(tmp_path / "does-not-exist") is None


def test_load_hash_empty_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "auth_password.hash"
    path.write_text("")
    assert load_hash(path) is None


def test_delete_hash_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "state" / "auth_password.hash"
    # Delete when missing — no error.
    delete_hash(path)
    # Delete after creating — removes the file.
    atomic_write_0600(path, hash_password("pw"))
    delete_hash(path)
    assert not path.exists()
