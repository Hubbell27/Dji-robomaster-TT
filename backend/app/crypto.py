"""Application-level envelope encryption for PHI.

Every encrypted value is stored as a self-describing blob:

    version(1) | wrapped_key_len(2) | wrapped_key | nonce(12) | AES-256-GCM ciphertext+tag

In production the per-record data key is generated and wrapped by AWS KMS
(``GenerateDataKey`` / ``Decrypt``) using a customer-managed key, so PHI in the
database is unreadable without KMS access, on top of RDS storage encryption.
Unwrapped data keys are cached briefly in memory to limit KMS calls.

Associated data (e.g. ``"intake:<id>:form"``) binds each ciphertext to its row
and purpose so blobs cannot be swapped between records.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import threading
import time
from functools import lru_cache
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_VERSION = 1


class KeyProvider(Protocol):
    def generate_data_key(self) -> tuple[bytes, bytes]:
        """Return (plaintext_key, wrapped_key)."""

    def unwrap(self, wrapped_key: bytes) -> bytes: ...


class LocalKeyProvider:
    """Dev/test only: wraps data keys with a static master key."""

    def __init__(self, master_key_b64: str):
        master = base64.b64decode(master_key_b64)
        if len(master) != 32:
            raise ValueError("INTAKE_LOCAL_DATA_KEY must be base64 of 32 bytes")
        self._aead = AESGCM(master)

    def generate_data_key(self) -> tuple[bytes, bytes]:
        key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        return key, nonce + self._aead.encrypt(nonce, key, b"local-wrap")

    def unwrap(self, wrapped_key: bytes) -> bytes:
        return self._aead.decrypt(wrapped_key[:12], wrapped_key[12:], b"local-wrap")


class KmsKeyProvider:
    _CACHE_TTL_SECONDS = 300
    _CACHE_MAX = 1024

    def __init__(self, key_id: str, region: str):
        import boto3

        self._key_id = key_id
        self._kms = boto3.client("kms", region_name=region)
        self._cache: dict[bytes, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    def generate_data_key(self) -> tuple[bytes, bytes]:
        resp = self._kms.generate_data_key(KeyId=self._key_id, KeySpec="AES_256")
        return resp["Plaintext"], resp["CiphertextBlob"]

    def unwrap(self, wrapped_key: bytes) -> bytes:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(wrapped_key)
            if hit and now - hit[0] < self._CACHE_TTL_SECONDS:
                return hit[1]
        key = self._kms.decrypt(CiphertextBlob=wrapped_key, KeyId=self._key_id)["Plaintext"]
        with self._lock:
            if len(self._cache) >= self._CACHE_MAX:
                self._cache.clear()
            self._cache[wrapped_key] = (now, key)
        return key


@lru_cache
def get_key_provider() -> KeyProvider:
    s = get_settings()
    if s.key_provider == "kms":
        return KmsKeyProvider(s.kms_key_id, s.aws_region)
    return LocalKeyProvider(s.local_data_key)


def encrypt_bytes(plaintext: bytes, aad: str) -> bytes:
    key, wrapped = get_key_provider().generate_data_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad.encode())
    return struct.pack(">BH", _VERSION, len(wrapped)) + wrapped + nonce + ct


def decrypt_bytes(blob: bytes, aad: str) -> bytes:
    version, wlen = struct.unpack(">BH", blob[:3])
    if version != _VERSION:
        raise ValueError(f"unsupported ciphertext version {version}")
    wrapped = blob[3 : 3 + wlen]
    nonce = blob[3 + wlen : 15 + wlen]
    ct = blob[15 + wlen :]
    key = get_key_provider().unwrap(wrapped)
    return AESGCM(key).decrypt(nonce, ct, aad.encode())


def encrypt_json(value: Any, aad: str) -> bytes:
    return encrypt_bytes(json.dumps(value, separators=(",", ":")).encode(), aad)


def decrypt_json(blob: bytes | None, aad: str) -> Any:
    if blob is None:
        return None
    return json.loads(decrypt_bytes(blob, aad))
