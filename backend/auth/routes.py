import os

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from backend.auth.service import authenticate, current_user, issue_token, TOKEN_SECONDS
from backend.db.event_repository import get_connection

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


def public_user(user: dict):
    return {key: user[key] for key in ("user_id", "display_name", "role")}


@router.post("/login")
def login(body: LoginRequest, response: Response):
    user = authenticate(body.user_id, body.password)
    token = issue_token(user)
    # Cookie is used only for read-only media; API mutations require Bearer auth.
    response.set_cookie("ppe_media", issue_token(user, "ppe-media"), httponly=True,
                        secure=os.environ.get("PPE_COOKIE_SECURE", "false").lower() == "true",
                        samesite="lax", path="/storage", max_age=TOKEN_SECONDS)
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token, **public_user(user)}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return public_user(user)


@router.post("/logout")
def logout(response: Response, user: dict = Depends(current_user)):
    with get_connection() as conn:
        conn.execute("UPDATE app_user SET auth_version=auth_version+1 WHERE user_id=?", (user["user_id"],))
    response.delete_cookie("ppe_media", path="/storage")
    return {"status": "ok"}
