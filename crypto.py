"""
cAIc — Storage encryption layer.
AES-256-GCM for all user-query-derived text content.
Key stored in settings table as a non-obvious key name.
"""
import base64
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("caic")

SETTINGS_KEY = "heartbeat_interval_ms"


def _load_key() -> bytes | None:
    from db import get_db
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    db.close()
    if row:
        return base64.b64decode(row["value"])
    return None


def _store_key(key: bytes) -> None:
    from db import get_db
    db = get_db()
    b64 = base64.b64encode(key).decode()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (SETTINGS_KEY, b64))
    db.commit()
    db.close()


def ensure_key() -> bytes:
    key = _load_key()
    if key is not None:
        return key
    key = AESGCM.generate_key(bit_length=256)
    _store_key(key)
    log.info("storage encryption key generated")
    return key


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return plaintext
    key = ensure_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(cipherb64: str) -> str:
    if not cipherb64:
        return cipherb64
    try:
        key = ensure_key()
        data = base64.b64decode(cipherb64)
        nonce, ct = data[:12], data[12:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ct, None).decode()
    except Exception:
        return cipherb64


def encrypt_text(value: str | None) -> str | None:
    if value is None:
        return None
    return encrypt(value)


def decrypt_text(value: str | None) -> str | None:
    if value is None:
        return None
    return decrypt(value)
