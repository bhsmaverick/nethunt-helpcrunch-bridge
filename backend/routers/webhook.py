import json
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Header, Depends
from typing import Optional

from ..database import get_settings, add_log
from ..services import helpcrunch
from ..services.sync import process_sync_task
from ..models.schemas import SimulateWebhookRequest
from .auth import get_current_user

router = APIRouter(tags=["webhook"])


@router.post("/api/webhook")
async def webhook_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    x_helpcrunch_signature: Optional[str] = Header(None)
):
    settings = get_settings()
    webhook_secret = settings.get("helpcrunch_webhook_secret", "")

    raw_body = await request.body()

    if webhook_secret and not helpcrunch.verify_signature(raw_body, x_helpcrunch_signature, webhook_secret):
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

    if chat_id is not None:
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            pass

    if not customer_data or not customer_data.get("id"):
        return {"status": "ignored", "reason": "No customer ID found in payload."}

    background_tasks.add_task(process_sync_task, event, customer_data, chat_id, message_text)
    return {"status": "queued", "event": event}


@router.post("/api/simulate-webhook")
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
