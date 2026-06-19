import hashlib
import hmac
import os
import time
import datetime
import pyotp
import logging
from typing import Optional

logger = logging.getLogger("bridge")

# Persistent session secret, loaded from the database in init_session_secret().
SESSION_SECRET = None

def init_session_secret():
    """Loads or creates the persistent session secret from the SQLite database."""
    global SESSION_SECRET
    from .database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            secret TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("SELECT secret FROM session_keys ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        SESSION_SECRET = bytes.fromhex(row["secret"])
        logger.info("Loaded persistent session secret from database.")
    else:
        secret = os.urandom(32)
        cursor.execute(
            "INSERT INTO session_keys (secret, created_at) VALUES (?, ?)",
            (secret.hex(), datetime.datetime.now().isoformat())
        )
        conn.commit()
        SESSION_SECRET = secret
        logger.info("Generated and persisted a new session secret in database.")
    conn.close()

def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """
    Hashes a password using PBKDF2-HMAC-SHA256.
    Returns (hex_hash, hex_salt).
    """
    if salt is None:
        salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return hashed.hex(), salt.hex()

def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    """Verifies a password against its stored PBKDF2 hash and salt."""
    try:
        salt = bytes.fromhex(salt_hex)
        hashed_attempt, _ = hash_password(password, salt)
        return hmac.compare_digest(hashed_attempt, password_hash)
    except Exception:
        logger.exception("Password verification error:")
        return False

# TOTP 2FA functions
def generate_totp_secret() -> str:
    """Generates a random base32 TOTP secret key."""
    return pyotp.random_base32()

def get_totp_uri(secret: str, username: str) -> str:
    """Generates the provisioning URI for TOTP QR Code scanning."""
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name="BridgeHC"
    )

def verify_totp_token(secret: str, token: str) -> bool:
    """Verifies a 6-digit TOTP token against the secret."""
    if not secret:
        return False
    try:
        totp = pyotp.totp.TOTP(secret)
        # valid_window=1 allows a drift of +/- 30 seconds
        return totp.verify(str(token).strip(), valid_window=1)
    except Exception:
        logger.exception("TOTP token verification error:")
        return False

# Stateless Session Token functions
def create_session_token(username: str) -> str:
    """Creates a signed session token: username:timestamp:signature."""
    timestamp = str(int(time.time()))
    message = f"{username}:{timestamp}"
    signature = hmac.new(SESSION_SECRET, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{message}:{signature}"

def verify_session_token(token: str, max_age: int = 86400) -> Optional[str]:
    """
    Verifies a session token.
    Returns the username if token is valid and not expired, else None.
    """
    if not token:
        return None
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
            
        username, timestamp_str, signature = parts
        timestamp = int(timestamp_str)
        
        # Check expiration (default 24 hours)
        if time.time() - timestamp > max_age:
            logger.warning(f"Session token expired for user {username}")
            return None
            
        message = f"{username}:{timestamp_str}"
        expected_sig = hmac.new(SESSION_SECRET, message.encode("utf-8"), hashlib.sha256).hexdigest()
        
        if hmac.compare_digest(expected_sig, signature):
            return username
    except Exception:
        logger.exception("Session token verification error:")
        
    return None
