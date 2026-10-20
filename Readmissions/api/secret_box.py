"""
Secrets stored in the database, encrypted: hospital database passwords
(api/ingest_connections.py) and AI provider keys (api/ai_settings.py).

The key is INGEST_SECRET_KEY, from the server's environment - never the
database - so a copy of the database alone does not give the secrets away. Any
string works; it is stretched to a Fernet key. Changing it makes everything
stored with the old one unreadable, and those secrets must be entered again.
"""
from __future__ import annotations

import base64
import hashlib
import os


class SecretKeyMissing(RuntimeError):
    """INGEST_SECRET_KEY is not set, so nothing can be stored safely."""


class SecretUnreadable(RuntimeError):
    """Stored with another key; it has to be entered again."""


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("INGEST_SECRET_KEY", "")
    if not key:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest()))


def ready() -> bool:
    return _fernet() is not None


def encrypt(text: str) -> str:
    f = _fernet()
    if f is None:
        raise SecretKeyMissing("INGEST_SECRET_KEY is not set on the server")
    return f.encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    from cryptography.fernet import InvalidToken
    f = _fernet()
    if f is None:
        raise SecretKeyMissing("INGEST_SECRET_KEY is not set on the server")
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise SecretUnreadable("the stored secret cannot be read (the server key changed); enter it again")
