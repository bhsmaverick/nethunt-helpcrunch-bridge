import logging
import json
import os
import sqlite3
import re
import traceback
from urllib.parse import urlparse, parse_qs
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Header, Depends, Response, Cookie
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# Import local modules
from .database import init_db, get_settings, save_settings, add_log, get_logs, get_metrics, get_db_connection, get_mirror_stats
from .services import nethunt, helpcrunch
from .extractors import extract_email, extract_phone, extract_messengers, extract_params_from_url, detect_platform_from_url
from . import auth, sync_engine

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bridge")

# Create App
app = FastAPI(title="BridgeHC - NetHunt & HelpCrunch Integration Hub")

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
    nethunt_base_url: Optional[str] = "https://nethunt.co"
    sync_priority: Optional[str] = "email,phone,telegram"
    telegram_field_hc: Optional[str] = "telegram"
    telegram_field_nh: Optional[str] = "Telegram"
    instagram_field_nh: Optional[str] = "Instagram"
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
    base_url: Optional[str] = "https://nethunt.co"

class FolderFieldsRequest(BaseModel):
    email: str
    key: str
    base_url: Optional[str] = "https://nethunt.co"
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
    nh_base = settings.get("nethunt_base_url", "https://nethunt.co")
    contacts_folder = settings.get("nethunt_contacts_folder")
    deals_folder = settings.get("nethunt_deals_folder")
    priority_str = settings.get("sync_priority", "email,phone,telegram")
    telegram_hc_key = settings.get("telegram_field_hc", "telegram")
    telegram_nh_key = settings.get("telegram_field_nh", "Telegram")
    instagram_nh_key = settings.get("instagram_field_nh", "Instagram")
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
    cust_name = customer_data.get("name") or "Unknown Customer"
    cust_email = customer_data.get("email") or ""
    cust_phone = customer_data.get("phone") or ""
    cust_referer = customer_data.get("referer") or ""
    cust_source = customer_data.get("source") or ""
    location_data = customer_data.get("location", {}) or {}
    cust_country = location_data.get("countryCode") or ""
    cust_city = location_data.get("city") or ""

    # Normalize customer's initial phone number
    if cust_phone:
        normalized_initial_phone = extract_phone(cust_phone)
        if normalized_initial_phone:
            cust_phone = normalized_initial_phone

    # Parse customData (for Telegram and UTM parameters)
    telegram_handle = ""
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
    
    # Try to extract messenger handles directly from referer or source URL path
    instagram_handle = ""
    
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

    # Extract info from message text if available
    extracted_email = None
    extracted_phone = None
    extracted_tg = None
    extracted_ig = None

    if message_text:
        extracted_email = extract_email(message_text)
        extracted_phone = extract_phone(message_text)
        messengers = extract_messengers(message_text)
        extracted_tg = messengers.get("telegram")
        extracted_ig = messengers.get("instagram")

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
        details_log.append(f"Extracted from message: Email='{extracted_email or ''}', Phone='{extracted_phone or ''}', Telegram='{extracted_tg or ''}', Instagram='{extracted_ig or ''}'")
    details_log.append(f"Merged fields: Email='{merged_email}', Phone='{merged_phone}', Telegram='{merged_telegram}', Instagram='{merged_instagram}'")
    details_log.append(f"Tracking: Source='{cust_source}', Referer='{cust_referer}', Country='{cust_country}', City='{cust_city}', Platform='{detected_platform}'")
    if utm_source or utm_medium or utm_campaign or gclid:
        details_log.append(f"UTMs: src='{utm_source}', med='{utm_medium}', cam='{utm_campaign}', gclid='{gclid}'")

    if not hc_api_key or not nh_email or not nh_key or not contacts_folder:
        err_msg = "Aborted: Credentials or folder mapping missing in Settings."
        details_log.append(err_msg)
        add_log(event_type, cust_name, merged_email, merged_phone, "error", "\n".join(details_log))
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
        # Save detected platform if available, otherwise fallback to URL
        tracking_fields[source_f] = detected_platform if detected_platform else (cust_source or "Organic/Direct")
    if country_f and cust_country: tracking_fields[country_f] = cust_country
    if city_f and cust_city: tracking_fields[city_f] = cust_city

    # Sequentially look up contact in NetHunt
    contact = None
    search_method_used = ""
    
    # STEP 0: Try the local mirror first (chat_link, phone, email, telegram, instagram)
    try:
        local_contact = await sync_engine.resolve_nh_contact(customer_data, chat_id)
        if local_contact and local_contact.get("raw_json"):
            contact = json.loads(local_contact["raw_json"])
            search_method_used = "Local Mirror"
            details_log.append(f"Matched existing NetHunt contact via local mirror: ID={local_contact.get('nh_record_id')}")
    except Exception:
        logger.exception("Local mirror contact resolution failed:")
    
    # STEP 1: Search by HelpCrunch ID if local mirror did not find a match
    if not contact and hc_id_nh_key and customer_id:
        details_log.append(f"Searching NetHunt by HelpCrunch ID: '{customer_id}' (Field: '{hc_id_nh_key}')...")
        # Exact field query syntax: Field_Name:"value"
        query_str = f'`{hc_id_nh_key}`:"{customer_id}"'
        contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
        if contact:
            search_method_used = "HelpCrunch ID"

    # Fallback to other priorities if not matched by ID
    if not contact:
        priorities = [p.strip() for p in priority_str.split(",") if p.strip()]
        for step in priorities:
            if step == "email" and merged_email and email_nh_key:
                details_log.append(f"Searching NetHunt by Email: '{merged_email}' (Field: '{email_nh_key}')...")
                query_str = f'`{email_nh_key}`:"{merged_email}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method_used = "Email"
                    break
            elif step == "phone" and merged_phone and phone_nh_key:
                details_log.append(f"Searching NetHunt by Phone: '{merged_phone}' (Field: '{phone_nh_key}')...")
                query_str = f'`{phone_nh_key}`:"{merged_phone}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method_used = "Phone"
                    break
            elif step == "telegram" and merged_telegram and telegram_nh_key:
                details_log.append(f"Searching NetHunt by Telegram: '{merged_telegram}' (Field: '{telegram_nh_key}')...")
                query_str = f'`{telegram_nh_key}`:"{merged_telegram}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method_used = "Telegram"
                    break

    is_new_contact = False
    if not contact:
        details_log.append("No matching contact found in NetHunt CRM. Creating a new contact card...")
        
        # Build payload fields for NetHunt create contact
        new_fields = {
            "Name": cust_name
        }
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
            
        # Append UTM tracking fields to create-record payload
        new_fields.update(tracking_fields)
            
        # If chat link update is configured, append the link immediately in creation payload
        if update_nh_link and chat_id and hc_subdomain:
            chat_url = f"https://{hc_subdomain.strip()}.helpcrunch.com/inbox/chats/{chat_id}"
            new_fields[nh_link_field] = chat_url
            
        created_contact = await nethunt.create_contact(nh_email, nh_key, nh_base, contacts_folder, new_fields)
        if created_contact:
            contact = created_contact
            is_new_contact = True
            search_method_used = "Auto-Created Card"
            details_log.append(f"Successfully created NetHunt Contact record ID: {contact.get('id')}")
            if tracking_fields:
                details_log.append(f"Wrote UTM & Referrer variables: {list(tracking_fields.keys())}")
        else:
            details_log.append("Failed to create new NetHunt contact card. Aborting.")
            add_log(event_type, cust_name, merged_email, merged_phone, "error", "\n".join(details_log))
            return
    else:
        # Existing contact was matched -> Update their UTM parameters AND any missing fields
        contact_id = contact.get("id")
        contact_fields = contact.get("fields", {})
        
        # Check for missing contact details in NetHunt to update them
        update_fields = {}
        if customer_id and hc_id_nh_key and not contact_fields.get(hc_id_nh_key):
            update_fields[hc_id_nh_key] = str(customer_id)
            details_log.append(f"Linking HelpCrunch ID to NetHunt contact: '{customer_id}'")
        if merged_email and email_nh_key and not contact_fields.get(email_nh_key):
            update_fields[email_nh_key] = [merged_email]
            details_log.append(f"Adding missing Email to NetHunt contact: '{merged_email}'")
        if merged_phone and phone_nh_key and not contact_fields.get(phone_nh_key):
            update_fields[phone_nh_key] = [merged_phone]
            details_log.append(f"Adding missing Phone to NetHunt contact: '{merged_phone}'")
        if merged_telegram and telegram_nh_key and not contact_fields.get(telegram_nh_key):
            update_fields[telegram_nh_key] = merged_telegram
            details_log.append(f"Adding missing Telegram handle to NetHunt contact: '{merged_telegram}'")
        if merged_instagram and instagram_nh_key and not contact_fields.get(instagram_nh_key):
            update_fields[instagram_nh_key] = merged_instagram
            details_log.append(f"Adding missing Instagram handle to NetHunt contact: '{merged_instagram}'")
            
        # Append tracking fields
        for k, v in tracking_fields.items():
            if not contact_fields.get(k):
                update_fields[k] = v
                
        if update_fields:
            details_log.append(f"Updating NetHunt CRM contact fields: {list(update_fields.keys())}...")
            updated = await nethunt.update_contact(nh_email, nh_key, nh_base, contact_id, update_fields)
            if updated:
                details_log.append("NetHunt contact updated successfully.")
            else:
                details_log.append("Warning: Could not update NetHunt contact fields.")

    contact_id = contact.get("id")
    contact_name = contact.get("name") or contact.get("fields", {}).get("Name") or cust_name
    details_log.append(f"Using NetHunt Contact: Name='{contact_name}', ID={contact_id} ({search_method_used})")

    # Build Contact Card Link
    contact_url = f"{nh_base}/app/records/{contacts_folder}/{contact_id}"
    details_log.append(f"NetHunt Contact Card URL: {contact_url}")

    # Bilateral Update: Update HelpCrunch customer profile with newly extracted/merged details if they were missing
    hc_update_payload = {}
    if merged_email and not cust_email:
        hc_update_payload["email"] = merged_email
    if merged_phone and not cust_phone:
        hc_update_payload["phone"] = merged_phone
    if merged_telegram and not telegram_handle:
        # HelpCrunch requires customData updates as a list of property/value dicts
        hc_update_payload["customData"] = [
            {"property": telegram_hc_key, "value": merged_telegram}
        ]
        
    if hc_update_payload:
        details_log.append(f"Bilateral sync: updating HelpCrunch customer profile {customer_id} with {list(hc_update_payload.keys())}...")
        hc_updated = await helpcrunch.update_customer(hc_api_key, customer_id, hc_update_payload)
        if hc_updated:
            details_log.append("HelpCrunch customer profile updated successfully.")
        else:
            details_log.append("Warning: HelpCrunch customer profile update failed.")

    # Fetch Deals
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
                
                # Deduce Stage field
                d_stage = "N/A"
                for field_name in ["Stage", "Deal Stage", "Status", "Pipeline Stage", "pipelineStage"]:
                    if field_name in deal_fields:
                        d_stage = str(deal_fields[field_name])
                        break
                
                # Deduce Amount field
                d_amount = ""
                for field_name in ["Amount", "Deal Amount", "Value", "value", "Price"]:
                    if field_name in deal_fields:
                        d_amount = f" - {deal_fields[field_name]}"
                        break
                        
                d_link = f"{nh_base}/app/records/{deals_folder}/{d_id}"
                deals.append(f"- {d_name}: Stage={d_stage}{d_amount} (Link: {d_link})")
                
            deals_text = "\n".join(deals)
            details_log.append(f"Found {len(deals_raw)} related deals.")
        else:
            details_log.append("No active deals found.")
    elif is_new_contact:
        deals_text = "- No deals found (newly created contact card) -"

    # Format Note to HelpCrunch
    card_prefix = "🟢 NetHunt Contact Card (NEW)" if is_new_contact else "🔴 NetHunt Contact Card"
    formatted_notes = (
        f"{card_prefix}: {contact_url}\n"
        f"👤 CRM Name: {contact_name}\n"
        f"💼 Related Deals:\n{deals_text}"
    )

    # 1. Update general customer notes in HelpCrunch
    details_log.append("Updating HelpCrunch customer notes...")
    notes_updated = await helpcrunch.update_customer_notes(hc_api_key, customer_id, formatted_notes)
    if notes_updated:
        details_log.append("Customer notes updated successfully in HelpCrunch.")
    else:
        details_log.append("Warning: HelpCrunch customer notes update failed.")

    # 2. Add private note in current chat window (if chat_id is present)
    if chat_id:
        chat_note = (
            f"🔗 **NetHunt Integration Hub**\n\n"
            f"Created New Contact: [{contact_name}]({contact_url})\n"
            f"Active Deals:\n{deals_text}"
        ) if is_new_contact else (
            f"🔗 **NetHunt Integration Hub**\n\n"
            f"Matched Contact: [{contact_name}]({contact_url})\n"
            f"Active Deals:\n{deals_text if deals else '- No deals found -'}"
        )
        details_log.append(f"Adding private note to chat ID {chat_id}...")
        private_note_added = await helpcrunch.add_private_note(hc_api_key, chat_id, chat_note)
        if private_note_added:
            details_log.append("Private note added to the chat inbox.")
        else:
            details_log.append("Warning: Could not add private note to the chat inbox.")

    # 3. Optional Bidirectional Sync (Only if not already updated in creation payload)
    if update_nh_link and chat_id and hc_subdomain and not is_new_contact:
        chat_url = f"https://{hc_subdomain.strip()}.helpcrunch.com/inbox/chats/{chat_id}"
        details_log.append(f"Bidirectional Sync: Writing Chat Link '{chat_url}' to NetHunt field '{nh_link_field}'...")
        nh_updated = await nethunt.update_contact_chat_link(nh_email, nh_key, nh_base, contact_id, nh_link_field, chat_url)
        if nh_updated:
            details_log.append("NetHunt CRM Contact updated with the HelpCrunch chat link.")
        else:
            details_log.append(f"Warning: Failed to update contact card field '{nh_link_field}' (ensure field exists in NetHunt Contacts folder).")

    add_log(event_type, cust_name, merged_email, merged_phone, "success", "\n".join(details_log))
    logger.info(f"Sync task completed successfully for customer {cust_name}")

    # Update local mirror after successful processing so future chats can resolve faster
    try:
        await sync_engine.update_mirror_from_webhook(customer_data, chat_id, contact_id)
    except Exception:
        logger.exception("Failed to update local mirror from webhook:")

async def process_sync_task(
    event_type: str,
    customer_data: dict,
    chat_id: Optional[int] = None,
    message_text: Optional[str] = None
):
    """Wrapper around _process_sync_task that catches unhandled exceptions and logs them."""
    customer_name = customer_data.get("name") or "Unknown Customer"
    customer_email = customer_data.get("email") or ""
    customer_phone = customer_data.get("phone") or ""
    try:
        await _process_sync_task(event_type, customer_data, chat_id, message_text)
    except Exception as e:
        logger.exception("Unhandled error during sync task:")
        error_details = f"Unhandled exception: {e}\n{traceback.format_exc()}"
        add_log(event_type, customer_name, customer_email, customer_phone, "error", error_details)
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
        add_log("webhook_rejected", "Unknown", "", "", "error", "Invalid signature in X-HelpCrunch-Signature header.")
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
        chat_id = event_data.get("id")
    elif event == "customer.new":
        customer_data = event_data
    elif event == "message.chat.customer":
        customer_data = event_data.get("customer") or {}
        chat_id = event_data.get("chat", {}).get("id") if isinstance(event_data.get("chat"), dict) else event_data.get("chat")
        message_text = event_data.get("message", {}).get("text")
    else:
        return {"status": "ignored", "reason": f"Unhandled event type: {event}"}
        
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
