"""Server-issued, signed thread IDs.

Callers are anonymous, so the thread ID is the only thing that ties a visitor to
their conversation. The server mints it and signs it; a caller cannot choose or
guess one that the server will accept.

A signed ID has two parts, `<key>.<signature>`:

- `key` is random and is what the service uses as the memory key and the
  Langfuse session ID. It is safe to log: without the signature it grants
  nothing.
- `signature` is an HMAC of the key with `THREAD_ID_SECRET`. The full signed ID
  is a credential, so it goes back to the caller and nowhere else. Never log it.
"""

import base64
import hashlib
import hmac
import logging
import secrets

from assistant import config

log = logging.getLogger("ai-starter")

_SEPARATOR = "."


def _load_secret() -> bytes:
    if config.settings.thread_id_secret:
        if len(config.settings.thread_id_secret) < 32:
            raise ValueError("THREAD_ID_SECRET must be at least 32 characters")
        return config.settings.thread_id_secret.encode()
    # Memory is in-process too, so conversations are lost on restart either way.
    # Any shared or long-lived deployment must set the secret.
    log.warning("THREAD_ID_SECRET is not set; using a random per-process secret")
    return secrets.token_bytes(32)


_secret = _load_secret()


def _sign(key: str) -> str:
    digest = hmac.new(_secret, key.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def issue() -> tuple[str, str]:
    """Mint a new thread. Returns `(signed_id, key)`."""
    key = secrets.token_urlsafe(16)
    return f"{key}{_SEPARATOR}{_sign(key)}", key


def verify(signed_id: str) -> str | None:
    """Return the key of a valid signed ID, or None if it is unsigned or forged."""
    key, sep, signature = signed_id.partition(_SEPARATOR)
    if not key or not sep or not signature:
        return None
    if not hmac.compare_digest(signature, _sign(key)):
        return None
    return key
