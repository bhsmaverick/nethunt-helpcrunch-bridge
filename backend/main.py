import logging
import json
import os
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Header
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# Import local modules
from .database import init_db, get_settings, save_settings, add_log, get_logs, get_metrics
from .services import nethunt, helpcrunch

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
    return "<h3>Error: index.html not found. Please create the frontend files first.</h3>"

# Mount static folder (will contain css & js)
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Request Models
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
    phone_field_nh: Optional[str] = "Phone"
    email_field_nh: Optional[str] = "Email"
    update_nh_chat_link: Optional[str] = "false"
    nh_chat_link_field: Optional[str] = "HelpCrunch Chat Link"

class TestConnectionRequest(BaseModel):
    email: Optional[str] = ""
    key: str
    base_url: Optional[str] = "https://nethunt.co"

class SimulateWebhookRequest(BaseModel):
    event: str
    name: str
    email: str
    phone: str
    telegram: str
    chat_id: Optional[int] = None

# Settings Endpoints
@app.get("/api/settings")
async def api_get_settings():
    return get_settings()

@app.post("/api/settings")
async def api_save_settings(payload: SettingsUpdate):
    try:
        updated = save_settings(payload.dict())
        return {"status": "success", "settings": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
async def api_get_logs(limit: int = 100, status: Optional[str] = None):
    return get_logs(limit=limit, status_filter=status)

@app.get("/api/metrics")
async def api_get_metrics():
    return get_metrics()

# Connection Test Endpoints
@app.post("/api/test-nethunt")
async def api_test_nethunt(payload: TestConnectionRequest):
    success = await nethunt.test_connection(payload.email, payload.key, payload.base_url)
    if success:
        return {"status": "success", "message": "Successfully connected to NetHunt CRM"}
    raise HTTPException(status_code=400, detail="Failed to connect to NetHunt CRM. Please check your credentials.")

@app.post("/api/test-helpcrunch")
async def api_test_helpcrunch(payload: TestConnectionRequest):
    success = await helpcrunch.test_connection(payload.key)
    if success:
        return {"status": "success", "message": "Successfully connected to HelpCrunch API"}
    raise HTTPException(status_code=400, detail="Failed to connect to HelpCrunch. Please check your credentials.")

@app.post("/api/nethunt/folders")
async def api_nethunt_folders(payload: TestConnectionRequest):
    folders = await nethunt.list_folders(payload.email, payload.key, payload.base_url)
    return folders

# Webhook Synchronizer Business Logic
async def process_sync_task(event_type: str, customer_data: dict, chat_id: Optional[int] = None):
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
    phone_nh_key = settings.get("phone_field_nh", "Phone")
    email_nh_key = settings.get("email_field_nh", "Email")
    update_nh_link = settings.get("update_nh_chat_link") == "true"
    nh_link_field = settings.get("nh_chat_link_field", "HelpCrunch Chat Link")
    hc_subdomain = settings.get("helpcrunch_subdomain", "")

    customer_id = customer_data.get("id")
    cust_name = customer_data.get("name") or "Unknown Customer"
    cust_email = customer_data.get("email") or ""
    cust_phone = customer_data.get("phone") or ""

    # Parse Telegram handle
    telegram_handle = ""
    custom_data = customer_data.get("customData")
    if custom_data:
        if isinstance(custom_data, list):
            for item in custom_data:
                if isinstance(item, dict):
                    prop = item.get("property") or item.get("name")
                    if prop == telegram_hc_key:
                        telegram_handle = item.get("value") or ""
                        break
        elif isinstance(custom_data, dict):
            telegram_handle = custom_data.get(telegram_hc_key) or ""
            
    # Remove @ from Telegram handle if present for clean search queries
    if telegram_handle and telegram_handle.startswith("@"):
        telegram_handle = telegram_handle[1:]

    details_log = []
    details_log.append(f"Starting processing for Event: {event_type}")
    details_log.append(f"Customer details: ID={customer_id}, Name='{cust_name}', Email='{cust_email}', Phone='{cust_phone}', Telegram='{telegram_handle}'")

    if not hc_api_key or not nh_email or not nh_key or not contacts_folder:
        err_msg = "Aborted: Credentials or folder mapping missing in Settings."
        details_log.append(err_msg)
        add_log(event_type, cust_name, cust_email, cust_phone, "error", "\n".join(details_log))
        logger.error(err_msg)
        return

    # Sequentially look up contact in NetHunt
    contact = None
    search_method_used = ""
    priorities = [p.strip() for p in priority_str.split(",") if p.strip()]

    for step in priorities:
        if step == "email" and cust_email:
            details_log.append(f"Searching NetHunt by Email: '{cust_email}'...")
            contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, cust_email)
            if contact:
                search_method_used = "Email"
                break
        elif step == "phone" and cust_phone:
            details_log.append(f"Searching NetHunt by Phone: '{cust_phone}'...")
            contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, cust_phone)
            if contact:
                search_method_used = "Phone"
                break
        elif step == "telegram" and telegram_handle:
            details_log.append(f"Searching NetHunt by Telegram: '{telegram_handle}'...")
            contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, telegram_handle)
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
        if cust_email and email_nh_key:
            new_fields[email_nh_key] = cust_email
        if cust_phone and phone_nh_key:
            new_fields[phone_nh_key] = cust_phone
        if telegram_handle and telegram_nh_key:
            new_fields[telegram_nh_key] = telegram_handle
            
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
        else:
            details_log.append("Failed to create new NetHunt contact card. Aborting.")
            add_log(event_type, cust_name, cust_email, cust_phone, "error", "\n".join(details_log))
            return

    contact_id = contact.get("id")
    contact_name = contact.get("name") or cust_name
    details_log.append(f"Using NetHunt Contact: Name='{contact_name}', ID={contact_id} ({search_method_used})")

    # Build Contact Card Link
    contact_url = f"{nh_base}/app/records/{contacts_folder}/{contact_id}"
    details_log.append(f"NetHunt Contact Card URL: {contact_url}")

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


    add_log(event_type, cust_name, cust_email, cust_phone, "success", "\n".join(details_log))
    logger.info(f"Sync task completed successfully for customer {cust_name}")

# Webhook Handler Endpoint
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
    
    if event == "chat.new":
        customer_data = event_data.get("customer") or {}
        chat_id = event_data.get("id")
    elif event == "customer.new":
        customer_data = event_data
    elif event == "message.chat.customer":
        # Supports syncing when user sends their first message in a chat
        customer_data = event_data.get("customer") or {}
        chat_id = event_data.get("chat", {}).get("id") if isinstance(event_data.get("chat"), dict) else event_data.get("chat")
    else:
        # Ignore other event types quietly to prevent webhooks retry loops
        return {"status": "ignored", "reason": f"Unhandled event type: {event}"}
        
    if not customer_data or not customer_data.get("id"):
        return {"status": "ignored", "reason": "No customer ID found in payload."}
        
    # Queue processing to keep HTTP response sub-second
    background_tasks.add_task(process_sync_task, event, customer_data, chat_id)
    
    return {"status": "queued", "event": event}

# Simulate / Trigger manual tests from Frontend UI
@app.post("/api/simulate-webhook")
async def simulate_webhook(payload: SimulateWebhookRequest, background_tasks: BackgroundTasks):
    custom_data = []
    if payload.telegram:
        settings = get_settings()
        tg_field = settings.get("telegram_field_hc", "telegram")
        custom_data.append({"property": tg_field, "value": payload.telegram})
        
    mock_customer = {
        "id": 9999999,
        "name": payload.name,
        "email": payload.email,
        "phone": payload.phone,
        "customData": custom_data
    }
    
    background_tasks.add_task(process_sync_task, payload.event, mock_customer, payload.chat_id)
    return {"status": "queued", "message": "Manual simulation task queued."}
