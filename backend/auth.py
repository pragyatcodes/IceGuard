"""JWT auth (FastAPI dependency) + password hashing. Roles: viewer/operator/admin."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET = os.environ.get("ICEGUARD_SECRET", "iceguard-sih2026-dev-secret-change-me")
ALGO = "HS256"
TOKEN_HOURS = 12

bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str, salt: str = "iceguard::") -> str:
    return hashlib.pbkdf2_hmac("sha256", (salt + pw).encode(), b"moes-ncpor", 120_000).hex()


def verify_password(pw: str, digest: str) -> bool:
    return hash_password(pw) == digest


def make_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "role": role,
               "iat": int(now.timestamp()),
               "exp": int((now + timedelta(hours=TOKEN_HOURS)).timestamp())}
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required")
    try:
        data = jwt.decode(creds.credentials, SECRET, algorithms=[ALGO])
        return {"username": data["sub"], "role": data.get("role", "viewer")}
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")


def need_roles(*roles: str):
    def guard(user: dict = Depends(current_user)):
        if user["role"] not in roles and user["role"] != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Requires role: {'/'.join(roles)} (you are {user['role']})")
        return user
    return guard
