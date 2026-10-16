"""Login with a proper web form, session cookie, lockout after repeated failures."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

import db

COOKIE = "areview_session"
SESSION_DAYS = 14
MAX_FAILS = 8           # per IP per 15 minutes


def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1,
                             maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(got.hex(), h)
    except Exception:
        return False


def client_ip(req: Request) -> str:
    xff = req.headers.get("x-forwarded-for")
    return xff.split(",")[0].strip() if xff else (req.client.host if req.client else "?")


def too_many_failures(ip: str) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
    db.ex("DELETE FROM login_failures WHERE at < ?", (since,))
    n = db.q1("SELECT COUNT(*) n FROM login_failures WHERE ip=?", (ip,))["n"]
    return n >= MAX_FAILS


def record_failure(ip: str):
    db.ex("INSERT INTO login_failures(ip, at) VALUES(?,?)", (ip, db.now()))


def create_session(user_id: int) -> str:
    tok = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    db.ex("INSERT INTO sessions(token,user_id,created,expires) VALUES(?,?,?,?)",
          (hashlib.sha256(tok.encode()).hexdigest(), user_id, db.now(), exp))
    return tok


def drop_session(tok: str):
    if tok:
        db.ex("DELETE FROM sessions WHERE token=?", (hashlib.sha256(tok.encode()).hexdigest(),))


def current_user(req: Request):
    tok = req.cookies.get(COOKIE)
    if not tok:
        return None
    s = db.q1("SELECT * FROM sessions WHERE token=?", (hashlib.sha256(tok.encode()).hexdigest(),))
    if not s or s["expires"] < db.now():
        return None
    return db.q1("SELECT id, username FROM users WHERE id=?", (s["user_id"],))


def require_user(req: Request):
    u = current_user(req)
    if not u:
        raise HTTPException(401, "not logged in")
    # CSRF: state-changing API calls must come from our own JS (custom header, SameSite=Strict cookie)
    if req.method not in ("GET", "HEAD") and req.headers.get("x-areview") != "1":
        raise HTTPException(403, "bad request origin")
    return u


def ensure_user(username: str, password: str | None):
    """Create the user if missing. Returns the generated password if one was created."""
    if db.q1("SELECT id FROM users WHERE username=?", (username,)):
        return None
    pw = password or secrets.token_urlsafe(15)
    db.ex("INSERT INTO users(username,pw_hash,created) VALUES(?,?,?)", (username, hash_pw(pw), db.now()))
    return pw
