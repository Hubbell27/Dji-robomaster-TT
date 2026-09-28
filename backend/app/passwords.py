"""Password hashing (scrypt) and TOTP (RFC 6238) for office-mode staff login."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

_N, _R, _P = 2**15, 8, 1
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Burn comparable time so unknown accounts are not distinguishable by timing.
        hash_password(password)
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                   maxmem=64 * 1024 * 1024, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, base64.b64decode(digest))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    classes = sum([any(c.islower() for c in password), any(c.isupper() for c in password),
                   any(c.isdigit() for c in password), any(not c.isalnum() for c in password)])
    if classes < 3:
        return "Use at least three of: lowercase, uppercase, numbers, symbols."
    return None


def temporary_password() -> str:
    # Readable, strong enough for a one-time handoff (must be changed at first sign-in).
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{body[:4]}-{body[4:8]}-{body[8:]}!"


# ---------------------------------------------------------------------------- TOTP

STEP = 30


def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def totp_now(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // STEP))


def verify_totp(secret: str, code: str, last_step: int | None, at: float | None = None) -> int | None:
    """Return the matched time step (to prevent replay), or None.

    Accepts one step of clock drift either way; rejects any step at or before
    ``last_step`` so a code cannot be reused.
    """
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        return None
    now = int((at or time.time()) // STEP)
    for step in (now - 1, now, now + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}"
            f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period={STEP}")
