import os
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.db.event_repository import get_connection

PASSWORDS = PasswordHasher()
DUMMY_HASH = PASSWORDS.hash("not-a-real-account")
bearer = HTTPBearer(auto_error=False)
TOKEN_SECONDS = 8 * 60 * 60


def secret() -> str:
    value = os.environ.get("PPE_JWT_SECRET", "")
    if len(value.encode()) < 32:
        raise HTTPException(503, "PPE_JWT_SECRET must contain at least 32 bytes")
    return value


def find_user(user_id: str):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM app_user WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_user(user_id: str, password: str, display_name: str, role: str) -> None:
    if role not in {"admin", "worker"} or len(password) < 8:
        raise ValueError("Valid role and password of at least 8 characters required")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO app_user(user_id,password_hash,display_name,role) VALUES(?,?,?,?)",
            (user_id, PASSWORDS.hash(password), display_name, role),
        )


def authenticate(user_id: str, password: str):
    user = find_user(user_id)
    try:
        PASSWORDS.verify(user["password_hash"] if user else DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        user = None
    if not user or not user["is_active"]:
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    return user


def issue_token(user: dict, audience: str = "ppe-api") -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user["user_id"], "iat": now, "exp": now + timedelta(seconds=TOKEN_SECONDS),
         "iss": "ppe-server", "aud": audience, "ver": user["auth_version"]},
        secret(), algorithm="HS256",
    )


def decode_user(token: str, audience: str = "ppe-api"):
    try:
        claims = jwt.decode(token, secret(), algorithms=["HS256"], audience=audience,
                            issuer="ppe-server", options={"require": ["sub", "exp", "iat", "ver"]})
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    user = find_user(claims["sub"])
    if not user or not user["is_active"] or user["auth_version"] != claims["ver"]:
        raise HTTPException(401, "Inactive or expired session")
    return user


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    return decode_user(credentials.credentials)


def require_admin(user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator access required")
    return user
