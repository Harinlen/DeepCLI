"""Password hashing and verification backed by ``hashlib.scrypt``.

Why scrypt and not bcrypt/argon2?
---------------------------------

- **Stdlib only** — ``hashlib.scrypt`` ships with CPython, so we
  pick up no third-party runtime dependency for a security-critical
  primitive.  ``bcrypt`` and ``argon2-cffi`` are both C extensions
  with their own supply-chain surface area; we prefer to borrow
  OpenSSL's (already in stdlib via ``hashlib``).
- **Parameter-tunable** — the ``n`` cost factor is embedded in the
  hash string so we can ratchet it over time without breaking
  existing installs.  The parse-side accepts any ``n / r / p`` the
  file specifies; we only hard-code defaults for **new** hashes.
- **Constant-time verify** — scrypt itself is not timing-safe, but
  our verification recomputes the full hash with the stored
  parameters and then compares fixed-length derived keys via
  :func:`secrets.compare_digest`, which is.

Hash serialization format
-------------------------

::

    scrypt$<n>$<r>$<p>$<salt_b64>$<key_b64>

- ``$``-separated rather than ``:`` so the field never collides
  with an IPv6 literal should we ever reuse this format elsewhere.
- Fields in the same order as the ``hashlib.scrypt`` parameters,
  followed by the derived key.
- Base64 (urlsafe, stripped padding) for salt/key so the whole line
  is pure ASCII and grep-friendly.
- Leading ``scrypt`` tag lets a future implementation dispatch on
  algorithm if we ever migrate, without a flag day.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

# Parameters used for **new** hashes.  Verification honors whatever
# the stored hash specifies, so raising these here only affects hashes
# created from now on — existing deployments keep working.
#
# ``n = 2**15`` is the same order of magnitude as the default
# recommendation in RFC 7914 for interactive login latency on modern
# hardware.  Bumping it 2× every few years is the intended upgrade
# path.
_DEFAULT_N = 2**15
_DEFAULT_R = 8
_DEFAULT_P = 1
_DERIVED_KEY_LEN = 64
_SALT_LEN = 16

# Scrypt memory usage is ≈ ``128 * n * r * p`` bytes; with the
# defaults above that is exactly 32 MiB, which equals OpenSSL's
# default ``maxmem`` cap and makes ``hashlib.scrypt`` raise
# ``ValueError: memory limit exceeded``.  We allow up to 128 MiB so
# the defaults fit with comfortable headroom and a future bump of
# ``_DEFAULT_N`` does not silently break verification of old hashes
# that were produced under the same (or smaller) parameters.
_SCRYPT_MAXMEM = 128 * 1024 * 1024

_ALGORITHM_TAG = "scrypt"
_FIELD_SEPARATOR = "$"


def hash_password(plaintext: str) -> str:
    """Hash ``plaintext`` with fresh salt and default scrypt params.

    Returns the serialized ``scrypt$n$r$p$salt$key`` string ready
    to be written to disk.  The caller is responsible for persisting
    it somewhere only the kernel process can read (``0o600``).
    """
    salt = os.urandom(_SALT_LEN)
    key = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=_DEFAULT_N,
        r=_DEFAULT_R,
        p=_DEFAULT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_DERIVED_KEY_LEN,
    )
    return _serialize(_DEFAULT_N, _DEFAULT_R, _DEFAULT_P, salt, key)


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """Return ``True`` iff ``plaintext`` hashes to ``stored_hash``.

    Uses the parameters recorded in ``stored_hash`` rather than the
    current defaults, so an upgrade of the defaults does not
    invalidate previously-stored passwords.  Any parse error returns
    ``False`` — a malformed hash is never a positive match — and
    the specific reason is logged at debug level for operators.

    The final comparison goes through
    :func:`secrets.compare_digest` so a wrong password never leaks
    information through response timing.
    """
    try:
        n, r, p, salt, expected_key = _deserialize(stored_hash)
    except ValueError as exc:
        logger.debug("auth: refusing malformed password hash: %s", exc)
        return False

    candidate_key = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_SCRYPT_MAXMEM,
        dklen=len(expected_key),
    )
    return secrets.compare_digest(candidate_key, expected_key)


def load_hash(path: Path) -> str | None:
    """Return the stored hash at ``path``, or ``None`` if missing.

    A missing file means "password auth is disabled" — that is a
    valid state, not an error, so callers branch on ``None`` rather
    than catching an exception.  A file that exists but contains
    only whitespace is treated the same as missing; we log a
    warning because it almost certainly indicates a botched manual
    edit that needs human attention.
    """
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        logger.warning(
            "auth: password hash file %s is empty — treating as disabled",
            path,
        )
        return None
    return content


def delete_hash(path: Path) -> None:
    """Remove the stored hash if present.

    Called by :meth:`kernel.connection_auth.ConnectionAuthenticator.clear_password` to
    switch the kernel into the "no remote password" state.  Missing
    file is not an error — the end state is the same.
    """
    path.unlink(missing_ok=True)


# --------------------------------------------------------------------
# Serialization helpers — kept module-private so the exact format
# can evolve (e.g. add a version prefix) without breaking callers.
# --------------------------------------------------------------------


def _serialize(n: int, r: int, p: int, salt: bytes, key: bytes) -> str:
    """Render the canonical ``scrypt$...`` string for one hash."""
    return _FIELD_SEPARATOR.join(
        [
            _ALGORITHM_TAG,
            str(n),
            str(r),
            str(p),
            _b64encode(salt),
            _b64encode(key),
        ]
    )


def _deserialize(stored_hash: str) -> tuple[int, int, int, bytes, bytes]:
    """Parse ``stored_hash`` into ``(n, r, p, salt, key)``.

    Raises
    ------
    ValueError
        If the algorithm tag is unknown, the field count is wrong,
        or any numeric / base64 field fails to decode.  The error
        message is only used for debug logging inside
        :func:`verify_password`; it never reaches the exception
        surface of :class:`kernel.connection_auth.ConnectionAuthenticator`.
    """
    parts = stored_hash.split(_FIELD_SEPARATOR)
    if len(parts) != 6:
        raise ValueError(f"expected 6 fields in password hash, got {len(parts)}")
    tag, n_str, r_str, p_str, salt_b64, key_b64 = parts
    if tag != _ALGORITHM_TAG:
        raise ValueError(f"unsupported algorithm tag: {tag!r}")
    try:
        n = int(n_str)
        r = int(r_str)
        p = int(p_str)
    except ValueError as exc:
        raise ValueError(f"non-integer scrypt parameter: {exc}") from exc
    if n <= 0 or r <= 0 or p <= 0:
        raise ValueError("scrypt parameters must be positive")
    try:
        salt = _b64decode(salt_b64)
        key = _b64decode(key_b64)
    except ValueError as exc:
        raise ValueError(f"bad base64 in password hash: {exc}") from exc
    return n, r, p, salt, key


def _b64encode(data: bytes) -> str:
    """URL-safe base64 without padding.

    Padding is stripped so the serialized line stays purely in the
    set ``[A-Za-z0-9_-]`` plus the field separator ``$``.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


_URLSAFE_B64_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="
)


def _b64decode(text: str) -> bytes:
    """Inverse of :func:`_b64encode` — re-adds padding before decode.

    :func:`base64.urlsafe_b64decode` silently drops characters
    outside the alphabet by default, which would make a garbage
    input like ``"!!!"`` decode to empty bytes and pass
    :func:`_deserialize` without complaint.  We want a malformed
    hash to fail loudly, so we validate the character set before
    handing the string to the decoder.
    """
    if not text or any(ch not in _URLSAFE_B64_ALPHABET for ch in text):
        raise ValueError("invalid base64 characters")
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except Exception as exc:
        raise ValueError(str(exc)) from exc
