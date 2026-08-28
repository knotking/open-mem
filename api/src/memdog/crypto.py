"""Envelope encryption for stored credentials.

Nothing can safely store a credential before this exists, which is why it comes
before the producer registry rather than after it.

The contract is that it **fails closed**: with no root key configured, storing a
credential raises rather than writing plaintext, and reading one raises rather
than returning something the caller might treat as a secret.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings


class CryptoUnavailable(RuntimeError):
    """No root key. Callers must not degrade to plaintext."""


class Envelope:
    """AES-GCM data key, wrapped by a root key.

    Local and GKE hold the root key in the environment or a k8s secret; the GCP
    variant swaps this class for a KMS-backed one implementing the same two
    methods. The wire format is deliberately the same so a re-key is a rewrap,
    not a re-encrypt of every credential.
    """

    VERSION = b"\x01"

    def __init__(self, master_key: bytes | None) -> None:
        if master_key is not None and len(master_key) not in (16, 24, 32):
            raise ValueError("master key must be 16, 24 or 32 bytes")
        self._master = master_key

    @classmethod
    def from_settings(cls, settings: Settings) -> "Envelope":
        raw = settings.master_key_b64
        return cls(base64.b64decode(raw) if raw else None)

    @property
    def available(self) -> bool:
        return self._master is not None

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        if self._master is None:
            raise CryptoUnavailable("MEMDOG_MASTER_KEY is not set")
        data_key = os.urandom(32)
        dk_nonce, ct_nonce = os.urandom(12), os.urandom(12)
        wrapped = AESGCM(self._master).encrypt(dk_nonce, data_key, aad)
        body = AESGCM(data_key).encrypt(ct_nonce, plaintext, aad)
        return (
            self.VERSION
            + dk_nonce
            + len(wrapped).to_bytes(2, "big")
            + wrapped
            + ct_nonce
            + body
        )

    def decrypt(self, blob: bytes, aad: bytes = b"") -> bytes:
        if self._master is None:
            raise CryptoUnavailable("MEMDOG_MASTER_KEY is not set")
        if not blob or blob[:1] != self.VERSION:
            raise ValueError("unrecognised ciphertext version")
        pos = 1
        dk_nonce, pos = blob[pos : pos + 12], pos + 12
        wrapped_len = int.from_bytes(blob[pos : pos + 2], "big")
        pos += 2
        wrapped, pos = blob[pos : pos + wrapped_len], pos + wrapped_len
        ct_nonce, pos = blob[pos : pos + 12], pos + 12
        data_key = AESGCM(self._master).decrypt(dk_nonce, wrapped, aad)
        return AESGCM(data_key).decrypt(ct_nonce, blob[pos:], aad)
