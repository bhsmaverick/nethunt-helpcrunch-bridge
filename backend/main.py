import logging
import json
import os
import sqlite3
import re
import asyncio
import traceback
from urllib.parse import urlparse, parse_qs
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Header, Depends, Response, Cookie
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# Import local modules
from .database import init_db, get_settings, save_settings, add_log, get_logs, get_metrics, get_db_connection, get_mirror_stats, find_hc_chats_by_customer_id
from .services import nethunt, helpcrunch
from .extractors import extract_email, extract_phone, extract_messengers, extract_params_from_url, detect_platform_from_url, build_chat_link, build_nethunt_record_url, extract_name
from . import auth, sync_engine

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bridge")

# Create App
app = FastAPI(title="BridgeHC - NetHunt & HelpCrunch Integration Hub")

# Per-customer locks to prevent concurrent webhook processing from creating duplicates
_customer_locks: dict = {}
_customer_locks_guard = asyncio.Lock()
_CUSTOMER_LOCKS_MAX = 500

# CORS Middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup Handler
@app.on_event("startup")
def startup_event():
    init_db()
    auth.init_session_secret()
    logger.info("Database initialized successfully.")

# Setup static directories
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
static_dir = os.path.join(frontend_dir, "static")

# Serve Index Page
@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return "<h3>Error: index.html not found.</h3>"

@app.get("/favicon.ico")
async def get_favicon():
    fav_path = os.path.join(static_dir, "favicon.png")
    if os.path.exists(fav_path):
        return FileResponse(fav_path)
    raise HTTPException(status_code=404)

# Mount static folder
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# --- Authentication Dependency ---
def get_current_user(session_id: Optional[str] = Cookie(None)):
    """Validates session cookie and returns username, else raises HTTP 401."""
    if not session_id:
        raise HTTPException(status_code=401, detail="Session cookie missing. Please log in.")
    username = auth.verify_session_token(session_id)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please log in again.")
    return username

# --- Pydantic Models ---
class SettingsUpdate(BaseModel):
    helpcrunch_api_key: str
    helpcrunch_subdomain: str
    helpcrunch_webhook_secret: Optional[str] = ""
    nethunt_api_email: str
    nethunt_api_key: str
    nethunt_contacts_folder: Optional[str] = ""
    nethunt_deals_folder: Optional[str] = ""
    nethunt_base_url: Optional[str] = "https://nethunt.com"
    nethunt_workspace_id: Optional[str] = ""
    sync_priority: Optional[str] = "email,phone,telegram"
    telegram_field_hc: Optional[str] = "telegram"
    telegram_field_nh: Optional[str] = "Telegram"
    instagram_field_nh: Optional[str] = "Instagram"
    name_field_nh: Optional[str] = "Name"
    phone_field_nh: Optional[str] = "Phone"
    email_field_nh: Optional[str] = "Email"
    hc_id_field_nh: Optional[str] = "HelpCrunch ID"
    update_nh_chat_link: Optional[str] = "false"
    nh_chat_link_field: Optional[str] = "HelpCrunch Chat Link"
    utm_source_field_nh: Optional[str] = "utm_source"
    utm_medium_field_nh: Optional[str] = "utm_medium"
    utm_campaign_field_nh: Optional[str] = "utm_campaign"
    utm_term_field_nh: Optional[str] = "utm_term"
    utm_content_field_nh: Optional[str] = "utm_content"
    gclid_field_nh: Optional[str] = "gclid"
    referer_field_nh: Optional[str] = "Referer"
    source_field_nh: Optional[str] = "Source"
    country_field_nh: Optional[str] = "Country"
    city_field_nh: Optional[str] = "City"

class TestConnectionRequest(BaseModel):
    email: Optional[str] = ""
    key: str
    base_url: Optional[str] = "https://nethunt.com"

class FolderFieldsRequest(BaseModel):
    email: str
    key: str
    base_url: Optional[str] = "https://nethunt.com"
    folder_id: str

class SimulateWebhookRequest(BaseModel):
    event: str
    name: str
    email: str
    phone: str
    telegram: str
    chat_id: Optional[int] = None
    utm_source: Optional[str] = ""
    utm_medium: Optional[str] = ""
    utm_campaign: Optional[str] = ""
    gclid: Optional[str] = ""

class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenVerificationRequest(BaseModel):
    username: str
    token: str

# --- Authentication API Routes ---

@app.get("/api/auth/status")
async def api_auth_status(session_id: Optional[str] = Cookie(None)):
    """Checks if the user is authenticated."""
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

@app.post("/api/auth/register")
async def api_auth_register(payload: RegisterRequest):
    """Registers the first admin user and generates TOTP 2FA secret."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Restrict registration to single tenant admin
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
    import segno
    import io
    import base64
    provisioning_uri = auth.get_totp_uri(totp_secret, payload.username)
    qr = segno.make(provisioning_uri)
    buf = io.BytesIO()
    qr.save(buf, kind='png', scale=4)
    qr_code_base64 = base64.b64encode(buf.getvalue()).decode()
    qr_code_data_uri = f"data:image/png;base64,{qr_code_base64}"
    return {
        "status": "success",
        "username": payload.username,
        "twofa_secret": totp_secret,
        "provisioning_uri": provisioning_uri,
        "qr_code_data_uri": qr_code_data_uri
    }

@app.post("/api/auth/verify-2fa")
async def api_auth_verify_2fa(payload: TokenVerificationRequest, response: Response):
    """Verifies the initial 2FA token during registration, enables 2FA, and sets session cookie."""
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
        
    # Enable 2FA on database
    cursor.execute("UPDATE users SET twofa_enabled = 1 WHERE username = ?", (payload.username,))
    conn.commit()
    conn.close()
    
    # Establish Session
    session_token = auth.create_session_token(payload.username)
    response.set_cookie(
        key="session_id",
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=False,  # Set True in production over HTTPS
        max_age=86400  # 24 hours
    )
    return {"status": "success", "username": payload.username}

@app.post("/api/auth/login")
async def api_auth_login(payload: LoginRequest):
    """Validates login credentials and checks if 2FA code is needed."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    row = cursor.execute(
        "SELECT password_hash, salt, twofa_enabled, twofa_secret FROM users WHERE username = ?",
        (payload.username,)
    )
    row = cursor.fetchone()
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
        # User registered but didn't scan QR code yet
        import segno
        import io
        import base64
        provisioning_uri = auth.get_totp_uri(twofa_secret, payload.username)
        qr = segno.make(provisioning_uri)
        buf = io.BytesIO()
        qr.save(buf, kind='png', scale=4)
        qr_code_base64 = base64.b64encode(buf.getvalue()).decode()
        qr_code_data_uri = f"data:image/png;base64,{qr_code_base64}"
        return {
            "status": "setup_2fa",
            "twofa_secret": twofa_secret,
            "provisioning_uri": provisioning_uri,
            "qr_code_data_uri": qr_code_data_uri
        }
        
    return {"status": "require_2fa"}

@app.post("/api/auth/login-2fa")
async def api_auth_login_2fa(payload: TokenVerificationRequest, response: Response):
    """Validates the 2FA token during login and establishes a session cookie."""
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
    
    # Establish Session
    session_token = auth.create_session_token(payload.username)
    response.set_cookie(
        key="session_id",
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=86400
    )
    return {"status": "success", "username": payload.username}

@app.post("/api/auth/logout")
async def api_auth_logout(response: Response, username: str = Depends(get_current_user)):
    """Logs the user out by deleting their session cookie."""
    response.delete_cookie(key="session_id")
    return {"status": "success", "message": "Successfully logged out."}


# --- Secured Settings & Logs Endpoints (Protected by get_current_user) ---

@app.get("/api/settings")
async def api_get_settings(username: str = Depends(get_current_user)):
    return get_settings()

@app.post("/api/settings")
async def api_save_settings(payload: SettingsUpdate, username: str = Depends(get_current_user)):
    try:
        updated = save_settings(payload.dict())
        return {"status": "success", "settings": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
async def api_get_logs(limit: int = 100, status: Optional[str] = None, username: str = Depends(get_current_user)):
    return get_logs(limit=limit, status_filter=status)

@app.get("/api/metrics")
async def api_get_metrics(username: str = Depends(get_current_user)):
    return get_metrics()

@app.post("/api/test-nethunt")
async def api_test_nethunt(payload: TestConnectionRequest, username: str = Depends(get_current_user)):
    success = await nethunt.test_connection(payload.email, payload.key, payload.base_url)
    if success:
        return {"status": "success", "message": "Successfully connected to NetHunt CRM"}
    raise HTTPException(status_code=400, detail="Failed to connect to NetHunt CRM. Please check your credentials.")

@app.post("/api/test-helpcrunch")
async def api_test_helpcrunch(payload: TestConnectionRequest, username: str = Depends(get_current_user)):
    success = await helpcrunch.test_connection(payload.key)
    if success:
        return {"status": "success", "message": "Successfully connected to HelpCrunch API"}
    raise HTTPException(status_code=400, detail="Failed to connect to HelpCrunch. Please check your credentials.")

@app.post("/api/nethunt/folders")
async def api_nethunt_folders(payload: TestConnectionRequest, username: str = Depends(get_current_user)):
    folders = await nethunt.list_folders(payload.email, payload.key, payload.base_url)
    return folders

@app.post("/api/nethunt/folder-fields")
async def api_nethunt_folder_fields(payload: FolderFieldsRequest, username: str = Depends(get_current_user)):
    fields = await nethunt.list_folder_fields(payload.email, payload.key, payload.base_url, payload.folder_id)
    return fields

@app.post("/api/sync/full")
async def api_sync_full(background_tasks: BackgroundTasks, username: str = Depends(get_current_user)):
    """Triggers a full historical sync of CRM and HelpCrunch data into the local mirror."""
    background_tasks.add_task(sync_engine.run_full_sync)
    return {"status": "queued", "message": "Full sync started in the background."}

@app.get("/api/sync/stats")
async def api_sync_stats(username: str = Depends(get_current_user)):
    """Returns counts of mirrored entities."""
    return get_mirror_stats()

# --- Webhook Synchronizer Business Logic ---

async def _get_customer_lock(customer_id):
    """Returns (or creates) an asyncio lock for a given customer_id."""
    async with _customer_locks_guard:
        # Cleanup: remove unlocked entries if dict is too large
        if len(_customer_locks) > _CUSTOMER_LOCKS_MAX:
            to_remove = [k for k, v in _customer_locks.items() if not v.locked()]
            for k in to_remove:
                del _customer_locks[k]
        if customer_id not in _customer_locks:
            _customer_locks[customer_id] = asyncio.Lock()
        return _customer_locks[customer_id]

async def _process_sync_task(
    event_type: str, 
    customer_data: dict, 
    chat_id: Optional[int] = None, 
    message_text: Optional[str] = None
):
    settings = get_settings()
    hc_api_key = settings.get("helpcrunch_api_key")
    nh_email = settings.get("nethunt_api_email")
    nh_key = settings.get("nethunt_api_key")
    nh_base = settings.get("nethunt_base_url", "https://nethunt.com")
    nh_workspace_id = settings.get("nethunt_workspace_id", "")
    contacts_folder = settings.get("nethunt_contacts_folder")
    deals_folder = settings.get("nethunt_deals_folder")
    priority_str = settings.get("sync_priority", "email,phone,telegram")
    telegram_hc_key = settings.get("telegram_field_hc", "telegram")
    telegram_nh_key = settings.get("telegram_field_nh", "Telegram")
    instagram_nh_key = settings.get("instagram_field_nh", "Instagram")
    name_nh_key = settings.get("name_field_nh", "Name")
    phone_nh_key = settings.get("phone_field_nh", "Phone")
    email_nh_key = settings.get("email_field_nh", "Email")
    hc_id_nh_key = settings.get("hc_id_field_nh", "HelpCrunch ID")
    update_nh_link = settings.get("update_nh_chat_link") == "true"
    nh_link_field = settings.get("nh_chat_link_field", "HelpCrunch Chat Link")
    hc_subdomain = settings.get("helpcrunch_subdomain", "")

    # Load UTM tracking key configurations
    utm_src_f = settings.get("utm_source_field_nh", "utm_source")
    utm_med_f = settings.get("utm_medium_field_nh", "utm_medium")
    utm_cam_f = settings.get("utm_campaign_field_nh", "utm_campaign")
    utm_trm_f = settings.get("utm_term_field_nh", "utm_term")
    utm_cnt_f = settings.get("utm_content_field_nh", "utm_content")
    gclid_f = settings.get("gclid_field_nh", "gclid")
    referer_f = settings.get("referer_field_nh", "Referer")
    source_f = settings.get("source_field_nh", "Source")
    country_f = settings.get("country_field_nh", "Country")
    city_f = settings.get("city_field_nh", "City")

    customer_id = customer_data.get("id")
    if customer_id is not None:
        try:
            customer_id = int(customer_id)
        except (ValueError, TypeError):
            pass
    cust_name = customer_data.get("name") or "Unknown Customer"
    cust_email = customer_data.get("email") or ""
    cust_phone = customer_data.get("phone") or ""
    cust_referer = customer_data.get("referer") or ""
    cust_source = customer_data.get("source") or ""
    location_data = customer_data.get("location", {}) or {}
    cust_country = location_data.get("countryCode") or ""
    cust_city = location_data.get("city") or ""

    # For message events, fetch full customer profile from HelpCrunch API
    # (webhook payload only contains basic id/name/email, no customData/phone/referer)
    if event_type == "message.chat.customer" and hc_api_key and customer_id:
        try:
            full_profile = await helpcrunch.get_customer(hc_api_key, customer_id)
            if full_profile and isinstance(full_profile, dict) and full_profile.get("id"):
                logger.info(f"Fetched full HC customer profile for message event: {full_profile.get('name', '')}")
                # Merge: only overwrite with non-None values from full_profile to avoid losing webhook data
                for k, v in full_profile.items():
                    if v is not None and v != "":
                        customer_data[k] = v
                cust_name = customer_data.get("name") or cust_name
                cust_email = customer_data.get("email") or ""
                cust_phone = customer_data.get("phone") or ""
                cust_referer = customer_data.get("referer") or ""
                cust_source = customer_data.get("source") or ""
                location_data = customer_data.get("location", {}) or {}
                cust_country = location_data.get("countryCode") or ""
                cust_city = location_data.get("city") or ""
        except Exception:
            logger.exception(f"Failed to fetch full HC customer profile for customer {customer_id}:")

    # Normalize customer's initial phone number
    if cust_phone:
        normalized_initial_phone = extract_phone(cust_phone)
        if normalized_initial_phone:
            cust_phone = normalized_initial_phone

    # Parse customData (for Telegram, Instagram, and UTM parameters)
    telegram_handle = ""
    instagram_handle = ""
    utm_source = ""
    utm_medium = ""
    utm_campaign = ""
    utm_term = ""
    utm_content = ""
    gclid = ""
    
    custom_data = customer_data.get("customData")
    if custom_data:
        if isinstance(custom_data, list):
            for item in custom_data:
                if isinstance(item, dict):
                    prop = item.get("property") or item.get("name")
                    val = item.get("value") or ""
                    if prop == telegram_hc_key:
                        telegram_handle = val
                    elif prop == "instagram":
                        instagram_handle = val
                    elif prop == "utm_source":
                        utm_source = val
                    elif prop == "utm_medium":
                        utm_medium = val
                    elif prop == "utm_campaign":
                        utm_campaign = val
                    elif prop == "utm_term":
                        utm_term = val
                    elif prop == "utm_content":
                        utm_content = val
                    elif prop == "gclid":
                        gclid = val
        elif isinstance(custom_data, dict):
            telegram_handle = custom_data.get(telegram_hc_key) or ""
            instagram_handle = custom_data.get("instagram") or ""
            utm_source = custom_data.get("utm_source") or ""
            utm_medium = custom_data.get("utm_medium") or ""
            utm_campaign = custom_data.get("utm_campaign") or ""
            utm_term = custom_data.get("utm_term") or ""
            utm_content = custom_data.get("utm_content") or ""
            gclid = custom_data.get("gclid") or ""

    # Parse query parameters from source and referer URLs
    source_params = extract_params_from_url(cust_source)
    referer_params = extract_params_from_url(cust_referer)

    # Merge extracted UTMs (priority: custom_data -> source_params -> referer_params)
    if not utm_source:
        utm_source = source_params.get("utm_source") or referer_params.get("utm_source") or ""
    if not utm_medium:
        utm_medium = source_params.get("utm_medium") or referer_params.get("utm_medium") or ""
    if not utm_campaign:
        utm_campaign = source_params.get("utm_campaign") or referer_params.get("utm_campaign") or ""
    if not utm_term:
        utm_term = source_params.get("utm_term") or referer_params.get("utm_term") or ""
    if not utm_content:
        utm_content = source_params.get("utm_content") or referer_params.get("utm_content") or ""
    if not gclid:
        gclid = source_params.get("gclid") or referer_params.get("gclid") or ""

    # Referrer/Source Platform & Handle Detection from URLs
    detected_platform = detect_platform_from_url(cust_referer) or detect_platform_from_url(cust_source)
    
    # Check if referer or source is Telegram link (t.me/handle)
    for url_str in [cust_source, cust_referer]:
        if url_str and "t.me/" in url_str.lower():
            try:
                parsed_url = urlparse(url_str)
                path = parsed_url.path.strip("/")
                if path and len(path) >= 5 and re.match(r'^[a-zA-Z0-9_]+$', path):
                    if path.lower() not in ["share", "joinchat", "addstickers", "c", "s"]:
                        telegram_handle = path
                        detected_platform = "Telegram"
                        break
            except Exception:
                pass
                
    # Check if referer or source is Instagram profile (instagram.com/handle)
    for url_str in [cust_source, cust_referer]:
        if url_str and "instagram.com/" in url_str.lower():
            try:
                parsed_url = urlparse(url_str)
                path = parsed_url.path.strip("/")
                if path and 1 <= len(path) <= 30 and re.match(r'^[a-zA-Z0-9_.]+$', path):
                    if path.lower() not in ["p", "reel", "stories", "explore", "direct"]:
                        instagram_handle = path
                        detected_platform = "Instagram"
                        break
            except Exception:
                pass

    # If platform detected from source/referer, set utm_medium accordingly if not already set
    if not utm_medium and detected_platform:
        if detected_platform == "Telegram":
            utm_medium = "Telegram"
        elif detected_platform == "Instagram":
            utm_medium = "Instagram"
        elif detected_platform == "Facebook":
            utm_medium = "Facebook"

    # Organic fallback: if no source, no referer, and no UTM, mark as organic
    if not utm_source and not utm_medium and not cust_source and not cust_referer and not gclid:
        utm_medium = "organic"

    # Extract info from message text if available
    extracted_email = None
    extracted_phone = None
    extracted_tg = None
    extracted_ig = None
    extracted_name = None

    if message_text:
        extracted_email = extract_email(message_text)
        extracted_phone = extract_phone(message_text)
        messengers = extract_messengers(message_text)
        extracted_tg = messengers.get("telegram")
        extracted_ig = messengers.get("instagram")
        extracted_name = extract_name(message_text)

    # Clean Telegram handle from prefix
    if telegram_handle and telegram_handle.startswith("@"):
        telegram_handle = telegram_handle[1:]

    # Merge extracted details (message/URL extraction overrides profile/custom data if profile is empty)
    merged_email = cust_email or extracted_email or ""
    merged_phone = cust_phone or extracted_phone or ""
    merged_telegram = telegram_handle or extracted_tg or ""
    merged_instagram = instagram_handle or extracted_ig or ""

    details_log = []
    details_log.append(f"Starting processing for Event: {event_type}")
    details_log.append(f"Customer info: ID={customer_id}, Name='{cust_name}', Email='{cust_email}', Phone='{cust_phone}', Telegram='{telegram_handle}', Instagram='{instagram_handle}'")
    if message_text:
        details_log.append(f"Parsed Message Text: '{message_text}'")
        details_log.append(f"Extracted from message: Email='{extracted_email or ''}', Phone='{extracted_phone or ''}', Telegram='{extracted_tg or ''}', Instagram='{extracted_ig or ''}', Name='{extracted_name or ''}'")
    details_log.append(f"Merged fields: Email='{merged_email}', Phone='{merged_phone}', Telegram='{merged_telegram}', Instagram='{merged_instagram}'")
    details_log.append(f"Tracking: Source='{cust_source}', Referer='{cust_referer}', Country='{cust_country}', City='{cust_city}', Platform='{detected_platform}'")
    if utm_source or utm_medium or utm_campaign or gclid:
        details_log.append(f"UTMs: src='{utm_source}', med='{utm_medium}', cam='{utm_campaign}', gclid='{gclid}'")

    if not hc_api_key or not nh_email or not nh_key or not contacts_folder:
        err_msg = "Aborted: Credentials or folder mapping missing in Settings."
        details_log.append(err_msg)
        add_log(event_type, cust_name, merged_email, merged_phone, "error", "\n".join(details_log), level="error", hc_customer_id=customer_id)
        logger.error(err_msg)
        return

    # Build UTM and tracking fields payload to update/write in NetHunt CRM
    tracking_fields = {}
    if utm_src_f and utm_source: tracking_fields[utm_src_f] = utm_source
    if utm_med_f and utm_medium: tracking_fields[utm_med_f] = utm_medium
    if utm_cam_f and utm_campaign: tracking_fields[utm_cam_f] = utm_campaign
    if utm_trm_f and utm_term: tracking_fields[utm_trm_f] = utm_term
    if utm_cnt_f and utm_content: tracking_fields[utm_cnt_f] = utm_content
    if gclid_f and gclid: tracking_fields[gclid_f] = gclid
    if referer_f and cust_referer: tracking_fields[referer_f] = cust_referer
    if source_f:
        tracking_fields[source_f] = detected_platform if detected_platform else (cust_source or "Organic/Direct")
    if country_f and cust_country: tracking_fields[country_f] = cust_country
    if city_f and cust_city: tracking_fields[city_f] = cust_city

    # Build chat URL if we have all the pieces (use build_chat_link for consistency)
    chat_url = ""
    if chat_id and hc_subdomain:
        chat_url = build_chat_link(hc_subdomain, chat_id)

    # STEP 1: Check local mirror — do we already have this user?
    contact = None
    search_method_used = ""
    
    # 1a. Match by chat_link in local mirror (highest priority)
    if chat_url:
        try:
            from .database import find_nh_contact_by_chat_link
            local_contact = find_nh_contact_by_chat_link(chat_url)
            if local_contact and local_contact.get("raw_json"):
                contact = json.loads(local_contact["raw_json"])
                search_method_used = "Local Mirror (chat_link)"
                details_log.append(f"Matched existing NetHunt contact via local mirror chat_link: ID={local_contact.get('nh_record_id')}")
        except Exception:
            logger.exception("Local mirror chat_link lookup failed:")

    # 1b. Check all user's chats in local mirror — for each chat with a chat_link, try to find the NH contact
    if not contact and customer_id:
        try:
            from .database import find_nh_contact_by_chat_link
            user_chats = find_hc_chats_by_customer_id(customer_id)
            for uc in user_chats:
                uc_chat_link = uc.get("chat_link") or ""
                if uc_chat_link:
                    local_contact = find_nh_contact_by_chat_link(uc_chat_link)
                    if local_contact and local_contact.get("raw_json"):
                        contact = json.loads(local_contact["raw_json"])
                        search_method_used = "Local Mirror (user chat history)"
                        details_log.append(f"Matched existing NetHunt contact via user's chat history (chat_link={uc_chat_link}): ID={local_contact.get('nh_record_id')}")
                        break
        except Exception:
            logger.exception("Local mirror user chat history lookup failed:")

    # 1c. Check if we know this HC customer — look at match_links table
    if not contact and customer_id:
        try:
            from .database import find_match_by_hc_customer_id, get_nh_contact_by_id
            match = find_match_by_hc_customer_id(customer_id)
            if match:
                local_contact = get_nh_contact_by_id(match["nh_contact_id"])
                if local_contact and local_contact.get("raw_json"):
                    contact = json.loads(local_contact["raw_json"])
                    search_method_used = "Local Mirror (HC customer match)"
                    details_log.append(f"Matched existing NetHunt contact via HC customer match: ID={local_contact.get('nh_record_id')}")
        except Exception:
            logger.exception("Local mirror HC customer match failed:")

    # 1d. Field-based local mirror match (phone, email, telegram, instagram)
    if not contact:
        try:
            local_contact = await sync_engine.resolve_nh_contact(customer_data, chat_id)
            if local_contact and local_contact.get("raw_json"):
                contact = json.loads(local_contact["raw_json"])
                search_method_used = "Local Mirror (field match)"
                details_log.append(f"Matched existing NetHunt contact via local mirror fields: ID={local_contact.get('nh_record_id')}")
        except Exception:
            logger.exception("Local mirror field-based resolution failed:")

    # STEP 2: Search NetHunt API directly if local mirror didn't find a match
    if not contact and hc_id_nh_key and customer_id:
        details_log.append(f"Searching NetHunt by HelpCrunch ID: '{customer_id}' (Field: '{hc_id_nh_key}')...")
        query_str = f'"{hc_id_nh_key}":"{customer_id}"'
        contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
        if contact:
            search_method_used = "HelpCrunch ID"

    if not contact:
        priorities = [p.strip() for p in priority_str.split(",") if p.strip()]
        for step in priorities:
            if step == "email" and merged_email and email_nh_key:
                details_log.append(f"Searching NetHunt by Email: '{merged_email}' (Field: '{email_nh_key}')...")
                query_str = f'"{email_nh_key}":"{merged_email}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method_used = "Email"
                    break
            elif step == "phone" and merged_phone and phone_nh_key:
                details_log.append(f"Searching NetHunt by Phone: '{merged_phone}' (Field: '{phone_nh_key}')...")
                query_str = f'"{phone_nh_key}":"{merged_phone}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method_used = "Phone"
                    break
            elif step == "telegram" and merged_telegram and telegram_nh_key:
                details_log.append(f"Searching NetHunt by Telegram: '{merged_telegram}' (Field: '{telegram_nh_key}')...")
                query_str = f'"{telegram_nh_key}":"{merged_telegram}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method_used = "Telegram"
                    break

    # STEP 3: Find or create the NetHunt contact
    is_new_contact = False
    if not contact:
        details_log.append("No matching contact found in NetHunt CRM. Creating a new contact card...")
        
        new_fields = {}
        if name_nh_key and cust_name:
            new_fields[name_nh_key] = cust_name
        if customer_id and hc_id_nh_key:
            new_fields[hc_id_nh_key] = str(customer_id)
        if merged_email and email_nh_key:
            new_fields[email_nh_key] = [merged_email]
        if merged_phone and phone_nh_key:
            new_fields[phone_nh_key] = [merged_phone]
        if merged_telegram and telegram_nh_key:
            new_fields[telegram_nh_key] = merged_telegram
        if merged_instagram and instagram_nh_key:
            new_fields[instagram_nh_key] = merged_instagram
            
        new_fields.update(tracking_fields)
            
        # Write chat link immediately in the creation payload — this is the FIRST thing we do
        if update_nh_link and chat_url:
            new_fields[nh_link_field] = chat_url
            details_log.append(f"Chat link '{chat_url}' will be written to field '{nh_link_field}' during contact creation.")
            
        created_contact, create_error = await nethunt.create_contact(nh_email, nh_key, nh_base, contacts_folder, new_fields)
        if created_contact:
            contact = created_contact
            is_new_contact = True
            search_method_used = "Auto-Created Card"
            details_log.append(f"Successfully created NetHunt Contact record ID: {contact.get('id')}")
            if tracking_fields:
                details_log.append(f"Wrote UTM & Referrer variables: {list(tracking_fields.keys())}")
        else:
            details_log.append("Failed to create new NetHunt contact card. Aborting.")
            if create_error:
                details_log.append(f"API error: {create_error}")
            add_log(event_type, cust_name, merged_email, merged_phone, "error", "\n".join(details_log), level="error", hc_customer_id=customer_id)
            return
    else:
        # Existing contact matched -> Update missing or append new values
        contact_id = contact.get("id")
        contact_fields = contact.get("fields", {})

        # Helper: get all existing values from a NetHunt field (handles list and scalar)
        def _existing_values(field_key):
            raw = contact_fields.get(field_key)
            if not raw:
                return []
            if isinstance(raw, list):
                return [str(v).strip().lower() for v in raw if v]
            return [str(raw).strip().lower()]

        # Helper: check if a new value already exists in field
        def _value_exists(field_key, new_val):
            return str(new_val).strip().lower() in _existing_values(field_key)

        # Fields to overwrite (for missing fields) and fields to append (for existing fields with new values)
        overwrite_fields = {}
        append_fields = {}
        notes_additions = []

        if customer_id and hc_id_nh_key and not contact_fields.get(hc_id_nh_key):
            overwrite_fields[hc_id_nh_key] = str(customer_id)
            details_log.append(f"Linking HelpCrunch ID to NetHunt contact: '{customer_id}'")

        # Email: if field empty -> overwrite; if has value and new is different -> append
        if merged_email and email_nh_key:
            if not contact_fields.get(email_nh_key):
                overwrite_fields[email_nh_key] = [merged_email]
                details_log.append(f"Adding missing Email to NetHunt contact: '{merged_email}'")
            elif not _value_exists(email_nh_key, merged_email):
                append_fields[email_nh_key] = [merged_email]
                details_log.append(f"Appending new Email to NetHunt contact: '{merged_email}'")

        # Phone: same logic
        if merged_phone and phone_nh_key:
            if not contact_fields.get(phone_nh_key):
                overwrite_fields[phone_nh_key] = [merged_phone]
                details_log.append(f"Adding missing Phone to NetHunt contact: '{merged_phone}'")
            elif not _value_exists(phone_nh_key, merged_phone):
                append_fields[phone_nh_key] = [merged_phone]
                details_log.append(f"Appending new Phone to NetHunt contact: '{merged_phone}'")

        # Telegram: same logic
        if merged_telegram and telegram_nh_key:
            if not contact_fields.get(telegram_nh_key):
                overwrite_fields[telegram_nh_key] = merged_telegram
                details_log.append(f"Adding missing Telegram handle to NetHunt contact: '{merged_telegram}'")
            elif not _value_exists(telegram_nh_key, merged_telegram):
                append_fields[telegram_nh_key] = merged_telegram
                details_log.append(f"Appending new Telegram handle to NetHunt contact: '{merged_telegram}'")

        # Instagram: same logic
        if merged_instagram and instagram_nh_key:
            if not contact_fields.get(instagram_nh_key):
                overwrite_fields[instagram_nh_key] = merged_instagram
                details_log.append(f"Adding missing Instagram handle to NetHunt contact: '{merged_instagram}'")
            elif not _value_exists(instagram_nh_key, merged_instagram):
                append_fields[instagram_nh_key] = merged_instagram
                details_log.append(f"Appending new Instagram handle to NetHunt contact: '{merged_instagram}'")

        # Tracking fields: only fill if missing
        for k, v in tracking_fields.items():
            if not contact_fields.get(k):
                overwrite_fields[k] = v

        # Step 1: Overwrite missing fields
        if overwrite_fields:
            details_log.append(f"Updating NetHunt CRM contact fields (overwrite): {list(overwrite_fields.keys())}...")
            updated = await nethunt.update_contact(nh_email, nh_key, nh_base, contact_id, overwrite_fields, overwrite=True)
            if updated:
                details_log.append("NetHunt contact updated successfully.")
            else:
                details_log.append("Warning: Could not update NetHunt contact fields.")

        # Step 2: Append new values to existing fields (overwrite=False to add to multi-value)
        if append_fields:
            details_log.append(f"Appending to NetHunt CRM contact fields: {list(append_fields.keys())}...")
            appended = await nethunt.update_contact(nh_email, nh_key, nh_base, contact_id, append_fields, overwrite=False)
            if appended:
                details_log.append("New values appended to NetHunt contact successfully.")
            else:
                # Fallback: write to notes field since append failed (field doesn't support multi-value)
                details_log.append("Append failed (field may not support multi-value). Writing to notes instead.")
                for k, v in append_fields.items():
                    if isinstance(v, list):
                        notes_additions.append(f"{k}: {', '.join(v)}")
                    else:
                        notes_additions.append(f"{k}: {v}")

        # Step 3: If we have notes additions, write them to a notes field in NetHunt
        if notes_additions:
            notes_text = " | ".join(notes_additions)
            notes_field_key = settings.get("nethunt_notes_field_nh", "Additional Info")
            notes_updated = await nethunt.update_contact(nh_email, nh_key, nh_base, contact_id, {notes_field_key: notes_text}, overwrite=False)
            if notes_updated:
                details_log.append(f"Additional info written to NetHunt field '{notes_field_key}'.")
            else:
                details_log.append(f"Warning: Could not write additional info to NetHunt field '{notes_field_key}'.")

    contact_id = contact.get("id")
    contact_fields = contact.get("fields", {})
    contact_name = contact.get("name") or sync_engine._first_value(contact_fields.get(name_nh_key)) or sync_engine._first_value(contact_fields.get("Name")) or cust_name
    details_log.append(f"Using NetHunt Contact: Name='{contact_name}', ID={contact_id} ({search_method_used})")

    # STEP 4: Write chat link to NetHunt IMMEDIATELY (for existing contacts — new contacts already have it)
    # This is done BEFORE deals/notes/bilateral sync to ensure the link is persisted as fast as possible
    if update_nh_link and chat_url and not is_new_contact:
        existing_link_raw = contact_fields.get(nh_link_field)
        existing_link = sync_engine._first_value(existing_link_raw).strip() if existing_link_raw else ""
        if existing_link != chat_url:
            details_log.append(f"Writing Chat Link '{chat_url}' to NetHunt field '{nh_link_field}' (priority write)...")
            nh_updated = await nethunt.update_contact_chat_link(nh_email, nh_key, nh_base, contact_id, nh_link_field, chat_url)
            if nh_updated:
                details_log.append("NetHunt CRM Contact updated with the HelpCrunch chat link.")
            else:
                details_log.append(f"Warning: Failed to update contact card field '{nh_link_field}' (ensure field exists in NetHunt Contacts folder).")
        else:
            details_log.append(f"Chat link already up-to-date in NetHunt field '{nh_link_field}'.")

    # STEP 5: Update local mirror IMMEDIATELY after chat link is written
    # This ensures future webhooks for the same chat can find the contact via chat_link
    try:
        await sync_engine.update_mirror_from_webhook(customer_data, chat_id, contact_id, contact)
        details_log.append("Local mirror updated with chat link and customer data.")
    except Exception:
        logger.exception("Failed to update local mirror from webhook:")

    # Build Contact Card Link (full base64 URL for customData + short URL for notes)
    contact_url = build_nethunt_record_url(nh_base, nh_workspace_id, contacts_folder, contact_id)
    short_contact_url = f"{nh_base}/app/records/{contacts_folder}/{contact_id}"
    details_log.append(f"NetHunt Contact Card URL: {contact_url}")

    # STEP 6: Bilateral Update — update HelpCrunch customer profile with extracted details
    # Also push NetHunt CRM data (name, email, phone) back to HelpCrunch if HC has empty/unknown values
    hc_update_payload = {}
    
    # Name: if HC has "Unknown Customer" but NetHunt has a real name, push it to HC
    # Also use extracted name from message if available
    effective_name = contact_name if (contact_name and contact_name != "Unknown Customer") else (extracted_name or cust_name)
    if contact_name and contact_name != "Unknown Customer" and (not cust_name or cust_name == "Unknown Customer"):
        hc_update_payload["name"] = contact_name
        details_log.append(f"Pushing NetHunt name '{contact_name}' to HelpCrunch customer profile.")
    elif extracted_name and (not cust_name or cust_name == "Unknown Customer"):
        hc_update_payload["name"] = extracted_name
        details_log.append(f"Pushing extracted name '{extracted_name}' to HelpCrunch customer profile.")
    
    # Email: if HC has no email but NetHunt does, push it
    nh_email_val = sync_engine._first_value(contact_fields.get(email_nh_key))
    if nh_email_val and not cust_email and not merged_email:
        merged_email = nh_email_val
        details_log.append(f"Using email from NetHunt CRM: '{merged_email}'")
    if merged_email and (not cust_email or extracted_email):
        hc_update_payload["email"] = merged_email
        
    # Phone: if HC has no phone but NetHunt does, push it
    nh_phone_val = sync_engine._first_value(contact_fields.get(phone_nh_key))
    if nh_phone_val and not cust_phone and not merged_phone:
        merged_phone = nh_phone_val
        details_log.append(f"Using phone from NetHunt CRM: '{merged_phone}'")
    if merged_phone and (not cust_phone or extracted_phone):
        hc_update_payload["phone"] = merged_phone

    # Build customData update — preserve existing entries, only add/update new ones
    custom_data_updates = []
    if merged_telegram and not telegram_handle:
        custom_data_updates.append({"property": telegram_hc_key, "value": merged_telegram})
    if merged_instagram and not instagram_handle:
        custom_data_updates.append({"property": "instagram", "value": merged_instagram})
    # Always add NetHunt contact URL to customData (no 255 char limit there)
    custom_data_updates.append({"property": "nethunt_contact_url", "value": contact_url})

    # Send email/phone update separately from customData
    # so customData failure doesn't block other updates
    if hc_update_payload:
        details_log.append(f"Bilateral sync: updating HelpCrunch customer profile {customer_id} with {list(hc_update_payload.keys())}...")
        hc_updated, hc_error = await helpcrunch.update_customer(hc_api_key, customer_id, hc_update_payload)
        if hc_updated:
            details_log.append("HelpCrunch customer profile updated successfully.")
        else:
            details_log.append(f"Warning: HelpCrunch customer profile update failed. {hc_error}")

    # Send customData separately
    if custom_data_updates:
        existing_custom_data = customer_data.get("customData") or []
        if isinstance(existing_custom_data, list):
            merged_cd = [dict(item) if isinstance(item, dict) else item for item in existing_custom_data]
            existing_props = {item.get("property") for item in merged_cd if isinstance(item, dict)}
            for update in custom_data_updates:
                if update["property"] not in existing_props:
                    merged_cd.append(update)
                else:
                    for item in merged_cd:
                        if isinstance(item, dict) and item.get("property") == update["property"]:
                            item["value"] = update["value"]
                            break
            cd_payload = {"customData": merged_cd}
        elif isinstance(existing_custom_data, dict):
            merged_cd = [{"property": k, "value": v} for k, v in existing_custom_data.items()]
            existing_props = set(existing_custom_data.keys())
            for update in custom_data_updates:
                if update["property"] not in existing_props:
                    merged_cd.append(update)
                else:
                    for item in merged_cd:
                        if item["property"] == update["property"]:
                            item["value"] = update["value"]
                            break
            cd_payload = {"customData": merged_cd}
        else:
            cd_payload = {"customData": custom_data_updates}

        cd_props = [item.get("property") for item in cd_payload["customData"] if isinstance(item, dict)]
        details_log.append(f"Updating HelpCrunch customData: {cd_props}...")
        cd_updated, cd_error = await helpcrunch.update_customer(hc_api_key, customer_id, cd_payload)
        if cd_updated:
            details_log.append("HelpCrunch customData updated successfully.")
        else:
            details_log.append(f"Warning: HelpCrunch customData update failed. {cd_error}")

    # STEP 7: Fetch Deals
    deals = []
    deals_text = "No deals associated."
    if deals_folder and not is_new_contact:
        details_log.append(f"Fetching deals from folder {deals_folder} associated with Contact ID {contact_id}...")
        deals_raw = await nethunt.find_deals(nh_email, nh_key, nh_base, deals_folder, contact_id)
        if deals_raw:
            deals = []
            for deal in deals_raw:
                deal_fields = deal.get("fields", {})
                d_id = deal.get("id")
                d_name = deal.get("name") or "Untitled Deal"
                
                d_stage = "N/A"
                for field_name in ["Stage", "Deal Stage", "Status", "Pipeline Stage", "pipelineStage"]:
                    if field_name in deal_fields:
                        d_stage = sync_engine._first_value(deal_fields[field_name])
                        break
                
                d_amount = ""
                for field_name in ["Amount", "Deal Amount", "Value", "value", "Price"]:
                    if field_name in deal_fields:
                        d_amount = f" - {sync_engine._first_value(deal_fields[field_name])}"
                        break
                        
                d_link = build_nethunt_record_url(nh_base, nh_workspace_id, deals_folder, d_id)
                deals.append(f"- {d_name}: Stage={d_stage}{d_amount} (Link: {d_link})")
                
            deals_text = "\n".join(deals)
            details_log.append(f"Found {len(deals_raw)} related deals.")
        else:
            details_log.append("No active deals found.")
    elif is_new_contact:
        deals_text = "- No deals found (newly created contact card) -"

    # STEP 8: Write NetHunt lead link back to HelpCrunch notes (max 255 chars)
    # Full URL is in customData; notes just have a short reference
    card_prefix = "🟢 NEW" if is_new_contact else "🔴"
    formatted_notes = f"{card_prefix} NetHunt: {contact_name} (ID: {contact_id})"
    if len(formatted_notes) > 255:
        formatted_notes = formatted_notes[:252] + "..."

    details_log.append("Updating HelpCrunch customer notes...")
    notes_updated, notes_error = await helpcrunch.update_customer_notes(hc_api_key, customer_id, formatted_notes)
    if notes_updated:
        details_log.append("Customer notes updated successfully in HelpCrunch.")
    else:
        details_log.append(f"Warning: HelpCrunch customer notes update failed. {notes_error}")

    # Add private note in current chat window (if chat_id is present)
    if chat_id:
        chat_note_md = (
            f"🔗 **NetHunt Integration Hub**\n\n"
            f"Created New Contact: [{contact_name}]({contact_url})\n"
            f"Active Deals:\n{deals_text}"
        ) if is_new_contact else (
            f"🔗 **NetHunt Integration Hub**\n\n"
            f"Matched Contact: [{contact_name}]({contact_url})\n"
            f"Active Deals:\n{deals_text if deals else '- No deals found -'}"
        )
        # Plain text version (without markdown)
        chat_note_plain = (
            f"NetHunt Integration Hub\n\n"
            f"{'Created New Contact' if is_new_contact else 'Matched Contact'}: {contact_name}\n"
            f"URL: {contact_url}\n"
            f"Active Deals: {deals_text if deals else '- No deals found -'}"
        )
        details_log.append(f"Adding private note to chat ID {chat_id}...")
        private_note_added, note_error = await helpcrunch.add_private_note(hc_api_key, chat_id, chat_note_plain, chat_note_md)
        if private_note_added:
            details_log.append("Private note added to the chat inbox.")
        else:
            details_log.append(f"Warning: Could not add private note to the chat inbox. {note_error}")

    log_level = "warning" if any("Warning:" in d for d in details_log) else "info"
    add_log(event_type, contact_name, merged_email, merged_phone, "success", "\n".join(details_log), level=log_level, hc_customer_id=customer_id)
    logger.info(f"Sync task completed successfully for customer {cust_name}")

async def process_sync_task(
    event_type: str,
    customer_data: dict,
    chat_id: Optional[int] = None,
    message_text: Optional[str] = None
):
    """Wrapper around _process_sync_task with per-customer locking and exception handling."""
    customer_name = customer_data.get("name") or "Unknown Customer"
    customer_email = customer_data.get("email") or ""
    customer_phone = customer_data.get("phone") or ""
    customer_id = customer_data.get("id")
    try:
        # Acquire per-customer lock to prevent concurrent duplicate creation
        if customer_id:
            lock = await _get_customer_lock(customer_id)
            async with lock:
                await _process_sync_task(event_type, customer_data, chat_id, message_text)
        else:
            await _process_sync_task(event_type, customer_data, chat_id, message_text)
    except Exception as e:
        logger.exception("Unhandled error during sync task:")
        error_details = f"Unhandled exception: {e}\n{traceback.format_exc()}"
        add_log(event_type, customer_name, customer_email, customer_phone, "error", error_details, level="error", hc_customer_id=customer_id)
        logger.error(f"Sync task failed for customer {customer_name}: {e}")

# Webhook Handler Endpoint (NOT protected - verified via HMAC)
@app.post("/api/webhook")
async def webhook_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    x_helpcrunch_signature: Optional[str] = Header(None)
):
    settings = get_settings()
    webhook_secret = settings.get("helpcrunch_webhook_secret", "")
    
    # Read raw body to verify HMAC signature
    raw_body = await request.body()
    
    # Verification
    if webhook_secret and not helpcrunch.verify_signature(raw_body, x_helpcrunch_signature, webhook_secret):
        logger.warning("Rejected HelpCrunch webhook: Signature mismatch.")
        add_log("webhook_rejected", "Unknown", "", "", "error", "Invalid signature in X-HelpCrunch-Signature header.", level="error", hc_customer_id=None)
        raise HTTPException(status_code=401, detail="Invalid signature header.")
        
    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
        
    event = payload.get("event")
    event_data = payload.get("eventData") or {}
    
    if not event:
        raise HTTPException(status_code=400, detail="Missing event name.")
        
    customer_data = {}
    chat_id = None
    
    message_text = None
    if event == "chat.new":
        customer_data = event_data.get("customer") or {}
        chat_id = event_data.get("chat_id") or event_data.get("id")
    elif event == "customer.new":
        customer_data = event_data
    elif event == "message.chat.customer":
        customer_data = event_data.get("customer") or {}
        chat_id = event_data.get("chat_id")
        message_text = event_data.get("message", {}).get("text")
    else:
        return {"status": "ignored", "reason": f"Unhandled event type: {event}"}

    # Ensure chat_id is int for HelpCrunch API
    if chat_id is not None:
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            pass
        
    if not customer_data or not customer_data.get("id"):
        return {"status": "ignored", "reason": "No customer ID found in payload."}
        
    # Queue processing to keep HTTP response sub-second
    background_tasks.add_task(process_sync_task, event, customer_data, chat_id, message_text)
    
    return {"status": "queued", "event": event}

# Simulate / Trigger manual tests (Protected by get_current_user)
@app.post("/api/simulate-webhook")
async def simulate_webhook(
    payload: SimulateWebhookRequest,
    background_tasks: BackgroundTasks,
    username: str = Depends(get_current_user)
):
    custom_data = []
    if payload.telegram:
        settings = get_settings()
        tg_field = settings.get("telegram_field_hc", "telegram")
        custom_data.append({"property": tg_field, "value": payload.telegram})
    
    # Append simulated UTMs
    if payload.utm_source:
        custom_data.append({"property": "utm_source", "value": payload.utm_source})
    if payload.utm_medium:
        custom_data.append({"property": "utm_medium", "value": payload.utm_medium})
    if payload.utm_campaign:
        custom_data.append({"property": "utm_campaign", "value": payload.utm_campaign})
    if payload.gclid:
        custom_data.append({"property": "gclid", "value": payload.gclid})
        
    mock_customer = {
        "id": 9999999,
        "name": payload.name,
        "email": payload.email,
        "phone": payload.phone,
        "customData": custom_data,
        "referer": "https://google.com/",
        "source": "https://example.com/landing-page",
        "location": {
            "countryCode": "UA",
            "city": "Kyiv"
        }
    }
    
    background_tasks.add_task(process_sync_task, payload.event, mock_customer, payload.chat_id)
    return {"status": "queued", "message": "Manual simulation task queued."}
