"""Password hashing with bcrypt in production and a stdlib scrypt fallback."""

import base64
import hashlib
import hmac
import secrets

try:
    import bcrypt as _bcrypt
except ImportError:  # pragma: no cover - exercised on minimal offline dev machines
    _bcrypt = None


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if _bcrypt is not None:
        return _bcrypt.hashpw(raw, _bcrypt.gensalt()).decode("ascii")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(raw, salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "$scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt + digest).decode("ascii")


def verify_password(password: str, stored: str) -> bool:
    raw = password.encode("utf-8")
    if stored.startswith("$2"):
        return _bcrypt is not None and _bcrypt.checkpw(raw, stored.encode("ascii"))
    if not stored.startswith("$scrypt$"):
        return False
    try:
        _, _, n, r, p, encoded = stored.split("$", 5)
        payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
        salt, expected = payload[:16], payload[16:]
        actual = hashlib.scrypt(raw, salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
