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
        details TEXT
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
        "nethunt_base_url": "https://nethunt.co",
        "sync_priority": "email,phone,telegram",
        "telegram_field_hc": "telegram",  # customData key
        "telegram_field_nh": "Telegram",   # NetHunt CRM field name
        "phone_field_nh": "Phone",        # NetHunt CRM field name
        "email_field_nh": "Email",        # NetHunt CRM field name
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
        "city_field_nh": "City"
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

def add_log(event_type, customer_name, customer_email, customer_phone, status, details):
    """Appends an event to the activity log and retains only the last 1000 items."""
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO logs (timestamp, event_type, customer_name, customer_email, customer_phone, status, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (timestamp, event_type, customer_name, customer_email, customer_phone, status, details)
    )
    
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
    """Retrieves sync metrics for the dashboard."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM logs")
    total_syncs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM logs WHERE status='success'")
    matched_syncs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM logs WHERE status='no_match'")
    unmatched_syncs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM logs WHERE status='error'")
    errors = cursor.fetchone()[0]
    
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
