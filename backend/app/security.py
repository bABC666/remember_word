from __future__ import annotations

import secrets
from enum import Enum
from functools import lru_cache

from pwdlib import PasswordHash

# Argon2id via pwdlib. No hand-rolled hashing, no plain SHA-256.
_PASSWORD_HASH = PasswordHash.recommended()

#: Stored for accounts without a usable password. The bootstrap admin is created
#: with this sentinel so no password material ever enters a migration file.
UNUSABLE_PASSWORD = "!"


class PasswordError(ValueError):
    pass


def hash_password(password: str) -> str:
    if not password:
        raise PasswordError("密码不能为空")
    return _PASSWORD_HASH.hash(password)


def password_is_usable(password_hash: str) -> bool:
    return bool(password_hash) and password_hash != UNUSABLE_PASSWORD


def verify_password(password: str, password_hash: str) -> bool:
    """Return True only for a real hash and a matching password.

    An empty, sentinel or otherwise unparseable hash never authenticates, so an
    account whose password has not been set cannot be logged into by any input.
    """
    from pwdlib.exceptions import UnknownHashError

    if not password_is_usable(password_hash) or not password:
        return False
    try:
        return _PASSWORD_HASH.verify(password, password_hash)
    except (UnknownHashError, ValueError):
        return False


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """A hash of a password that nobody knows, for constant-work login failures.

    Verifying the supplied password against this makes a login attempt for an
    unknown username cost the same as one for a known username, so response time
    cannot be used to enumerate accounts. The plaintext is generated here and
    discarded: it is never stored, never logged and never accepted, because every
    caller that verifies against this hash refuses the login regardless of the
    result.

    Computed on first use (one Argon2 hash, once per process) rather than at
    import time, so tools and migrations that merely import this module do not pay
    for it.
    """
    return hash_password(secrets.token_urlsafe(32))


class Role(str, Enum):
    admin = "admin"
    user = "user"
