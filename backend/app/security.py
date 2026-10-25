import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

settings = get_settings()
_ph = PasswordHasher()  # Argon2id, library defaults (OWASP-recommended)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


# A real hash of a random string, used to keep login timing constant when the
# email does not exist (prevents account enumeration by timing).
DUMMY_HASH = _ph.hash(secrets.token_hex(16))


def validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise ValueError("Password needs upper- and lower-case letters and a number")


def create_access_token(user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id), "org": str(org_id), "type": "access",
        "iat": now, "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES), "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_challenge_token(user_id: uuid.UUID) -> str:
    """Short-lived proof that the password was right; the 2FA code finishes sign-in."""
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "type": "2fa", "iat": now, "exp": now + timedelta(minutes=5)},
                      settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_challenge_token(token: str) -> uuid.UUID:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM], options={"require": ["exp", "sub", "type"]})
    if payload.get("type") != "2fa":
        raise jwt.InvalidTokenError("wrong token type")
    return uuid.UUID(payload["sub"])


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
                         options={"require": ["exp", "sub", "org", "type"]})
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def new_opaque_token() -> tuple[str, str]:
    """Returns (token for the client, sha256 hash to store)."""
    token = secrets.token_urlsafe(48)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Sliding-window limiter kept in process memory. Good for a single API
    instance; for several replicas put a shared limiter (e.g. Redis or the load
    balancer) in front — see docs/DEPLOYMENT.md."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
