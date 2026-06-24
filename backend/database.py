import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database tables if they do not exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    # Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        twofa_secret TEXT,
        twofa_enabled INTEGER DEFAULT 0
    )
    """)
    
    # Session keys table (for persistent session secret)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        secret TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    
    # Logs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        event_type TEXT,
        customer_name TEXT,
        customer_email TEXT,
        customer_phone TEXT,
        status TEXT,
        level TEXT,
        details TEXT,
        hc_customer_id TEXT
    )
    """)
    # Migrate existing logs tables that were created without the level or hc_customer_id column
    cursor.execute("PRAGMA table_info(logs)")
    existing_columns = [row[1] for row in cursor.fetchall()]
    if "level" not in existing_columns:
        cursor.execute("ALTER TABLE logs ADD COLUMN level TEXT")
        cursor.execute("UPDATE logs SET level = CASE status WHEN 'error' THEN 'error' WHEN 'success' THEN 'info' ELSE 'info' END")
    if "hc_customer_id" not in existing_columns:
        cursor.execute("ALTER TABLE logs ADD COLUMN hc_customer_id TEXT")

    # Sync counters table (persists across log cleanup)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sync_counters (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        total_syncs INTEGER DEFAULT 0,
        matched_syncs INTEGER DEFAULT 0,
        unmatched_syncs INTEGER DEFAULT 0,
        errors INTEGER DEFAULT 0
    )
    """)
    cursor.execute("INSERT OR IGNORE INTO sync_counters (id, total_syncs, matched_syncs, unmatched_syncs, errors) VALUES (1, 0, 0, 0, 0)")

    
    # Mirror: HelpCrunch customers
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hc_customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hc_customer_id INTEGER UNIQUE NOT NULL,
        name TEXT,
        email TEXT,
        phone TEXT,
        telegram TEXT,
        instagram TEXT,
        raw_json TEXT,
        first_seen_at TEXT,
        last_seen_at TEXT
    )
    """)
    
    # Mirror: HelpCrunch chats
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hc_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hc_chat_id INTEGER UNIQUE NOT NULL,
        hc_customer_id INTEGER NOT NULL,
        status TEXT,
        chat_link TEXT,
        raw_json TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    
    # Mirror: NetHunt contacts
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS nh_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nh_record_id TEXT UNIQUE NOT NULL,
        folder_id TEXT NOT NULL,
        name TEXT,
        email TEXT,
        phone TEXT,
        telegram TEXT,
        instagram TEXT,
        chat_link TEXT,
        hc_customer_id INTEGER,
        raw_json TEXT,
        synced_at TEXT
    )
    """)
    
    # Mirror: NetHunt deals
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS nh_deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nh_record_id TEXT UNIQUE NOT NULL,
        folder_id TEXT NOT NULL,
        contact_id TEXT,
        name TEXT,
        stage TEXT,
        amount TEXT,
        raw_json TEXT,
        synced_at TEXT
    )
    """)
    
    # Match links between HC customers and NetHunt contacts
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS match_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hc_customer_id INTEGER NOT NULL,
        nh_contact_id TEXT NOT NULL,
        matched_by TEXT NOT NULL,
        confidence TEXT,
        created_at TEXT,
        UNIQUE(hc_customer_id, nh_contact_id)
    )
    """)
    
    # Populate default settings if empty
    defaults = {
        "helpcrunch_api_key": "",
        "helpcrunch_subdomain": "",
        "helpcrunch_webhook_secret": "",
        "nethunt_api_email": "",
        "nethunt_api_key": "",
        "nethunt_contacts_folder": "",
        "nethunt_deals_folder": "",
        "nethunt_base_url": "https://nethunt.com",
        "nethunt_workspace_id": "",
        "sync_priority": "email,phone,telegram",
        "telegram_field_hc": "telegram",  # customData key
        "telegram_field_nh": "Telegram",   # NetHunt CRM field name
        "instagram_field_nh": "Instagram", # NetHunt CRM field name
        "name_field_nh": "Name",          # NetHunt CRM field name for contact name
        "phone_field_nh": "Phone",        # NetHunt CRM field name
        "email_field_nh": "Email",        # NetHunt CRM field name
        "hc_id_field_nh": "HelpCrunch ID", # NetHunt CRM field name for HC Customer ID
        "update_nh_chat_link": "false",   # Boolean string
        "nh_chat_link_field": "HelpCrunch Chat Link", # NetHunt CRM field name
        "utm_source_field_nh": "utm_source",
        "utm_medium_field_nh": "utm_medium",
        "utm_campaign_field_nh": "utm_campaign",
        "utm_term_field_nh": "utm_term",
        "utm_content_field_nh": "utm_content",
        "gclid_field_nh": "gclid",
        "referer_field_nh": "Referer",
        "source_field_nh": "Source",
        "country_field_nh": "Country",
        "city_field_nh": "City",
        "branch_field_nh": "",
        "branch_mapping": ""
    }
    
    for key, val in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    conn.commit()
    conn.close()

def get_settings():
    """Retrieves all settings as a dict."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    rows = cursor.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}

def save_settings(settings_dict):
    """Saves a dict of settings."""
    conn = get_db_connection()
    cursor = conn.cursor()
    for key, val in settings_dict.items():
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(val))
        )
    conn.commit()
    conn.close()
    return get_settings()

def add_log(event_type, customer_name, customer_email, customer_phone, status, details, level="info", hc_customer_id=None):
    """Upserts an event log per customer (one row per hc_customer_id) and increments sync counters only on new entries."""
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    is_new_log = True

    if hc_customer_id:
        cursor.execute("SELECT id, customer_name, customer_email, customer_phone FROM logs WHERE hc_customer_id = ? ORDER BY id DESC LIMIT 1", (str(hc_customer_id),))
        existing = cursor.fetchone()
        if existing:
            is_new_log = False
            existing_id, existing_name, existing_email, existing_phone = existing
            # Merge: only overwrite with non-empty new values; keep existing if new is empty
            final_name = customer_name if customer_name and customer_name != "Unknown Customer" else (existing_name or customer_name)
            final_email = customer_email if customer_email else (existing_email or "")
            final_phone = customer_phone if customer_phone else (existing_phone or "")
            cursor.execute(
                "UPDATE logs SET timestamp=?, event_type=?, customer_name=?, customer_email=?, customer_phone=?, status=?, level=?, details=? WHERE id=?",
                (timestamp, event_type, final_name, final_email, final_phone, status, level, details, existing_id)
            )
        else:
            cursor.execute(
                "INSERT INTO logs (timestamp, event_type, customer_name, customer_email, customer_phone, status, level, details, hc_customer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (timestamp, event_type, customer_name, customer_email, customer_phone, status, level, details, str(hc_customer_id))
            )
    else:
        cursor.execute(
            "INSERT INTO logs (timestamp, event_type, customer_name, customer_email, customer_phone, status, level, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (timestamp, event_type, customer_name, customer_email, customer_phone, status, level, details)
        )

    # Increment sync counters only on new log entries (not on updates of existing row)
    if is_new_log:
        cursor.execute("UPDATE sync_counters SET total_syncs = total_syncs + 1")
        if status == 'success':
            cursor.execute("UPDATE sync_counters SET matched_syncs = matched_syncs + 1")
        elif status == 'no_match':
            cursor.execute("UPDATE sync_counters SET unmatched_syncs = unmatched_syncs + 1")
        elif status == 'error':
            cursor.execute("UPDATE sync_counters SET errors = errors + 1")

    # Keep only last 1000 logs to prevent unbounded DB growth
    cursor.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 1000)")

    conn.commit()
    conn.close()

def get_logs(limit=100, status_filter=None):
    """Retrieves logs sorted by latest first."""
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM logs"
    params = []
    
    if status_filter:
        query += " WHERE status = ?"
        params.append(status_filter)
        
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def get_metrics():
    """Retrieves sync metrics from persistent counters."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT total_syncs, matched_syncs, unmatched_syncs, errors FROM sync_counters WHERE id = 1")
    row = cursor.fetchone()
    if row:
        total_syncs, matched_syncs, unmatched_syncs, errors = row
    else:
        total_syncs = matched_syncs = unmatched_syncs = errors = 0

    conn.close()

    match_rate = 0.0
    if total_syncs > 0:
        match_rate = round((matched_syncs / total_syncs) * 100, 1)

    return {
        "total_syncs": total_syncs,
        "matched_syncs": matched_syncs,
        "unmatched_syncs": unmatched_syncs,
        "errors": errors,
        "match_rate": match_rate
    }

# --- Mirror DB helpers ---

def _now():
    return datetime.now().isoformat()

def save_hc_customer(hc_customer_id, name, email, phone, telegram, instagram, raw_json):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute("""
        INSERT INTO hc_customers (hc_customer_id, name, email, phone, telegram, instagram, raw_json, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(hc_customer_id) DO UPDATE SET
            name=excluded.name,
            email=excluded.email,
            phone=excluded.phone,
            telegram=excluded.telegram,
            instagram=excluded.instagram,
            raw_json=excluded.raw_json,
            last_seen_at=excluded.last_seen_at
    """, (hc_customer_id, name, email, phone, telegram, instagram, raw_json, now, now))
    conn.commit()
    conn.close()

def save_hc_chat(hc_chat_id, hc_customer_id, status, chat_link, raw_json, created_at, updated_at):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO hc_chats (hc_chat_id, hc_customer_id, status, chat_link, raw_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(hc_chat_id) DO UPDATE SET
            hc_customer_id=excluded.hc_customer_id,
            status=excluded.status,
            chat_link=excluded.chat_link,
            raw_json=excluded.raw_json,
            created_at=excluded.created_at,
            updated_at=excluded.updated_at
    """, (hc_chat_id, hc_customer_id, status, chat_link, raw_json, created_at, updated_at))
    conn.commit()
    conn.close()

def save_nh_contact(nh_record_id, folder_id, name, email, phone, telegram, instagram, chat_link, hc_customer_id, raw_json):
    conn = get_db_connection()
    cursor = conn.cursor()
    synced_at = _now()
    cursor.execute("""
        INSERT INTO nh_contacts (nh_record_id, folder_id, name, email, phone, telegram, instagram, chat_link, hc_customer_id, raw_json, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(nh_record_id) DO UPDATE SET
            folder_id=excluded.folder_id,
            name=excluded.name,
            email=excluded.email,
            phone=excluded.phone,
            telegram=excluded.telegram,
            instagram=excluded.instagram,
            chat_link=excluded.chat_link,
            hc_customer_id=excluded.hc_customer_id,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
    """, (nh_record_id, folder_id, name, email, phone, telegram, instagram, chat_link, hc_customer_id, raw_json, synced_at))
    conn.commit()
    conn.close()

def save_nh_deal(nh_record_id, folder_id, contact_id, name, stage, amount, raw_json):
    conn = get_db_connection()
    cursor = conn.cursor()
    synced_at = _now()
    cursor.execute("""
        INSERT INTO nh_deals (nh_record_id, folder_id, contact_id, name, stage, amount, raw_json, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(nh_record_id) DO UPDATE SET
            folder_id=excluded.folder_id,
            contact_id=excluded.contact_id,
            name=excluded.name,
            stage=excluded.stage,
            amount=excluded.amount,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
    """, (nh_record_id, folder_id, contact_id, name, stage, amount, raw_json, synced_at))
    conn.commit()
    conn.close()

def save_match_link(hc_customer_id, nh_contact_id, matched_by, confidence="high"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO match_links (hc_customer_id, nh_contact_id, matched_by, confidence, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(hc_customer_id, nh_contact_id) DO UPDATE SET
            matched_by=excluded.matched_by,
            confidence=excluded.confidence,
            created_at=excluded.created_at
    """, (hc_customer_id, nh_contact_id, matched_by, confidence, _now()))
    conn.commit()
    conn.close()

def get_hc_customer_by_id(hc_customer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hc_customers WHERE hc_customer_id = ?", (hc_customer_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_hc_chat_by_id(hc_chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hc_chats WHERE hc_chat_id = ?", (hc_chat_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def find_hc_chats_by_customer_id(hc_customer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hc_chats WHERE hc_customer_id = ? ORDER BY updated_at DESC", (hc_customer_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows] if rows else []

def get_nh_contact_by_id(nh_contact_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM nh_contacts WHERE nh_record_id = ?", (nh_contact_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def find_nh_contact_by_chat_link(chat_link):
    if not chat_link:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM nh_contacts WHERE chat_link = ? LIMIT 1", (chat_link,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def find_nh_contact_by_phone(phone):
    if not phone:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM nh_contacts WHERE phone = ? LIMIT 1", (phone,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def find_nh_contact_by_email(email):
    if not email:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM nh_contacts WHERE email = ? LIMIT 1", (email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def find_nh_contact_by_telegram(telegram):
    if not telegram:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM nh_contacts WHERE telegram = ? LIMIT 1", (telegram,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def find_nh_contact_by_instagram(instagram):
    if not instagram:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM nh_contacts WHERE instagram = ? LIMIT 1", (instagram,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def find_match_by_hc_customer_id(hc_customer_id):
    if not hc_customer_id:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ml.*, nc.nh_record_id FROM match_links ml
        JOIN nh_contacts nc ON ml.nh_contact_id = nc.nh_record_id
        WHERE ml.hc_customer_id = ?
        LIMIT 1
    """, (hc_customer_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_mirror_stats():
    """Returns counts of mirrored entities for the dashboard."""
    conn = get_db_connection()
    cursor = conn.cursor()
    stats = {
        "hc_customers": cursor.execute("SELECT COUNT(*) FROM hc_customers").fetchone()[0],
        "hc_chats": cursor.execute("SELECT COUNT(*) FROM hc_chats").fetchone()[0],
        "nh_contacts": cursor.execute("SELECT COUNT(*) FROM nh_contacts").fetchone()[0],
        "nh_deals": cursor.execute("SELECT COUNT(*) FROM nh_deals").fetchone()[0],
        "match_links": cursor.execute("SELECT COUNT(*) FROM match_links").fetchone()[0],
    }
    conn.close()
    return stats

def clear_mirror_data():
    """Removes all mirrored data and match links. Useful before a full re-sync."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM hc_customers")
    cursor.execute("DELETE FROM hc_chats")
    cursor.execute("DELETE FROM nh_contacts")
    cursor.execute("DELETE FROM nh_deals")
    cursor.execute("DELETE FROM match_links")
    conn.commit()
    conn.close()
