"""App 自己的帳號系統（跟 Garmin 帳號無關），讓你和朋友各自登入。"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Header, HTTPException, Request

from . import db
from .config import SECRET_KEY, SESSION_DAYS

_fernet = Fernet(SECRET_KEY)


def encrypt(text: str) -> bytes:
    return _fernet.encrypt(text.encode())


def decrypt(blob: bytes | None) -> str | None:
    if not blob:
        return None
    try:
        return _fernet.decrypt(blob).decode()
    except InvalidToken:
        return None


def hash_pw(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return h, salt


def verify_pw(password: str, pw_hash: str, salt: str) -> bool:
    h, _ = hash_pw(password, salt)
    return hmac.compare_digest(h, pw_hash)


def create_user(username: str, password: str, display_name: str | None = None) -> int:
    h, salt = hash_pw(password)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO users(username, display_name, pw_hash, pw_salt, cal_token) VALUES(?,?,?,?,?)",
            (username, display_name or username, h, salt, secrets.token_urlsafe(24)),
        )
        return cur.lastrowid


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    with db.tx() as c:
        c.execute("INSERT INTO sessions(token, user_id, expires_at) VALUES(?,?,?)", (token, user_id, exp))
    return token


def delete_session(token: str):
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


def current_user(request: Request, authorization: str | None = Header(default=None)) -> dict:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    token = token or request.cookies.get("ic8_session")
    if not token:
        raise HTTPException(401, "請先登入")
    row = db.q1(
        "SELECT u.* , s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
        (token,),
    )
    if not row or row["expires_at"] < datetime.now(timezone.utc).isoformat():
        raise HTTPException(401, "登入已過期，請重新登入")
    return row
