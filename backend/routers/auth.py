import sqlite3
import segno
import io
import base64
from fastapi import APIRouter, HTTPException, Response, Cookie, Depends
from typing import Optional

from ..database import get_db_connection
from .. import auth
from ..models.schemas import RegisterRequest, LoginRequest, TokenVerificationRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_current_user(session_id: Optional[str] = Cookie(None)):
    """Validates session cookie and returns username, else raises HTTP 401."""
    if not session_id:
        raise HTTPException(status_code=401, detail="Session cookie missing. Please log in.")
    username = auth.verify_session_token(session_id)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please log in again.")
    return username


def _generate_qr_data_uri(provisioning_uri: str) -> str:
    qr = segno.make(provisioning_uri)
    buf = io.BytesIO()
    qr.save(buf, kind='png', scale=4)
    qr_code_base64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{qr_code_base64}"


@router.get("/status")
async def auth_status(session_id: Optional[str] = Cookie(None)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    conn.close()

    if total_users == 0:
        return {"status": "unregistered", "message": "No users registered yet. Registration is open."}

    if not session_id:
        return {"status": "unauthenticated"}

    username = auth.verify_session_token(session_id)
    if username:
        return {"status": "authenticated", "username": username}

    return {"status": "unauthenticated"}


@router.post("/register")
async def register(payload: RegisterRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] > 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Registration is closed. Administrator already exists.")

    pwd_hash, salt = auth.hash_password(payload.password)
    totp_secret = auth.generate_totp_secret()

    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, salt, twofa_secret, twofa_enabled) VALUES (?, ?, ?, ?, 0)",
            (payload.username, pwd_hash, salt, totp_secret)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists.")

    conn.close()
    provisioning_uri = auth.get_totp_uri(totp_secret, payload.username)
    qr_code_data_uri = _generate_qr_data_uri(provisioning_uri)
    return {
        "status": "success",
        "username": payload.username,
        "twofa_secret": totp_secret,
        "provisioning_uri": provisioning_uri,
        "qr_code_data_uri": qr_code_data_uri
    }


@router.post("/verify-2fa")
async def verify_2fa(payload: TokenVerificationRequest, response: Response):
    conn = get_db_connection()
    cursor = conn.cursor()

    row = cursor.execute("SELECT twofa_secret, twofa_enabled FROM users WHERE username = ?", (payload.username,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="User not found.")

    secret = row["twofa_secret"]

    if not auth.verify_totp_token(secret, payload.token):
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid 2FA token.")

    cursor.execute("UPDATE users SET twofa_enabled = 1 WHERE username = ?", (payload.username,))
    conn.commit()
    conn.close()

    session_token = auth.create_session_token(payload.username)
    response.set_cookie(
        key="session_id", value=session_token,
        httponly=True, samesite="lax", secure=False, max_age=86400
    )
    return {"status": "success", "username": payload.username}


@router.post("/login")
async def login(payload: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    row = cursor.execute(
        "SELECT password_hash, salt, twofa_enabled, twofa_secret FROM users WHERE username = ?",
        (payload.username,)
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=400, detail="Invalid username or password.")

    pwd_hash = row["password_hash"]
    salt = row["salt"]
    twofa_enabled = row["twofa_enabled"]
    twofa_secret = row["twofa_secret"]

    if not auth.verify_password(payload.password, pwd_hash, salt):
        raise HTTPException(status_code=400, detail="Invalid username or password.")

    if not twofa_enabled:
        provisioning_uri = auth.get_totp_uri(twofa_secret, payload.username)
        qr_code_data_uri = _generate_qr_data_uri(provisioning_uri)
        return {
            "status": "setup_2fa",
            "twofa_secret": twofa_secret,
            "provisioning_uri": provisioning_uri,
            "qr_code_data_uri": qr_code_data_uri
        }

    return {"status": "require_2fa"}


@router.post("/login-2fa")
async def login_2fa(payload: TokenVerificationRequest, response: Response):
    conn = get_db_connection()
    cursor = conn.cursor()

    row = cursor.execute("SELECT twofa_secret FROM users WHERE username = ?", (payload.username,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="User not found.")

    secret = row["twofa_secret"]

    if not auth.verify_totp_token(secret, payload.token):
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid 2FA token.")

    conn.close()

    session_token = auth.create_session_token(payload.username)
    response.set_cookie(
        key="session_id", value=session_token,
        httponly=True, samesite="lax", secure=False, max_age=86400
    )
    return {"status": "success", "username": payload.username}


@router.post("/logout")
async def logout(response: Response, username: str = Depends(get_current_user)):
    response.delete_cookie(key="session_id")
    return {"status": "success", "message": "Successfully logged out."}
