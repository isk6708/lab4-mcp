"""JWT access tokens (HS256) for the /secure API.

A token is a signed JSON payload: {"sub": "<username>", "iat": ..., "exp": ...}.
Anyone can read it (it is only base64), but nobody can change it without JWT_SECRET,
so the API can trust the username inside until the token expires.
"""
import os
import time
from typing import Optional, Tuple

import jwt

ALGORITHM = "HS256"


def _secret(secret: Optional[str]) -> str:
    return secret or os.getenv("JWT_SECRET", "dev-only-jwt-secret-change-me-min-32-chars")


def token_minutes() -> int:
    return int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))


def create_access_token(username: str, minutes: Optional[int] = None,
                        secret: Optional[str] = None) -> Tuple[str, int]:
    """Return (token, lifetime in seconds)."""
    lifetime = (minutes if minutes is not None else token_minutes()) * 60
    now = int(time.time())
    payload = {"sub": username, "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, _secret(secret), algorithm=ALGORITHM), lifetime


class InvalidToken(Exception):
    pass


def decode_access_token(token: str, secret: Optional[str] = None) -> str:
    """Return the username in a valid token, or raise InvalidToken (bad signature, expired, malformed)."""
    try:
        payload = jwt.decode(token, _secret(secret), algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
    except jwt.ExpiredSignatureError as exc:
        raise InvalidToken("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken("Invalid token") from exc
    username = payload.get("sub")
    if not isinstance(username, str) or not username:
        raise InvalidToken("Invalid token")
    return username
