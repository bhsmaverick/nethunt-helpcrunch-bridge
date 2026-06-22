import requests
import json
import hmac
import hashlib
import time
import sys

URL = "http://127.0.0.1:8091/api/webhook"
SECRET = "" # If you set a webhook signing key in settings, specify it here to compute header signature

def generate_signature(body_bytes, secret):
    return hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha1
    ).hexdigest()

def send_mock_chat_webhook(name, email, phone, telegram, chat_id=77777):
    # Construct eventData per HelpCrunch docs: chat_id is a direct field, not "id"
    payload = {
        "event": "chat.new",
        "eventData": {
            "chat_id": chat_id,
            "status": "new",
            "createdAt": int(time.time()),
            "customer": {
                "id": 88888,
                "name": name,
                "email": email,
                "phone": phone,
                "customData": [
                    {
                        "property": "telegram",
                        "value": telegram
                    }
                ],
                "referer": "https://google.com/",
                "source": "https://example.com/landing-page?utm_source=test&utm_medium=cpc&utm_campaign=test_campaign"
            }
        },
        "attempts": 1,
        "lastAttemptAt": int(time.time()),
        "createdAt": int(time.time()),
        "id": "evt_mock_chat_123"
    }
    
    body_bytes = json.dumps(payload).encode("utf-8")
    
    headers = {
        "Content-Type": "application/json"
    }
    
    if SECRET:
        sig = generate_signature(body_bytes, SECRET)
        headers["X-HelpCrunch-Signature"] = sig
        print(f"Generated Signature: {sig}")
        
    print(f"Sending webhook mock payload for customer {name} (Email: {email}, Phone: {phone}, TG: {telegram}) to {URL}...")
    try:
        response = requests.post(URL, data=body_bytes, headers=headers)
        print(f"Server Response: Status {response.status_code}")
        print(response.json())
    except Exception as e:
        print(f"Connection failed: {e}")

def send_mock_message_webhook(name, email, phone, telegram, chat_id=77777, message_text="My phone is +380501112233, email: test@test.com"):
    # Construct eventData per HelpCrunch message webhook docs: chat_id is direct field
    payload = {
        "event": "message.chat.customer",
        "eventData": {
            "chat_id": chat_id,
            "agent": None,
            "customer": {
                "id": 88888,
                "name": name,
                "email": email,
                "userId": "test_user_88888"
            },
            "message": {
                "applicationId": 77,
                "broadcastType": None,
                "createdAt": str(time.time()),
                "edited": False,
                "id": 38488,
                "read": False,
                "text": message_text,
                "updatedAt": str(time.time())
            }
        },
        "attempts": 0,
        "lastAttemptAt": int(time.time()),
        "createdAt": int(time.time()),
        "id": "evt_mock_msg_456"
    }
    
    body_bytes = json.dumps(payload).encode("utf-8")
    
    headers = {
        "Content-Type": "application/json"
    }
    
    if SECRET:
        sig = generate_signature(body_bytes, SECRET)
        headers["X-HelpCrunch-Signature"] = sig
        print(f"Generated Signature: {sig}")
        
    print(f"Sending message webhook mock payload for customer {name} (chat_id: {chat_id}) to {URL}...")
    try:
        response = requests.post(URL, data=body_bytes, headers=headers)
        print(f"Server Response: Status {response.status_code}")
        print(response.json())
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    name = "John Doe"
    email = "john.doe@example.com"
    phone = "+380501112233"
    telegram = "johndoe_tg"
    event_type = "chat"
    
    args = sys.argv[1:]
    if "--message" in args:
        event_type = "message"
        args.remove("--message")
    
    if len(args) > 0:
        name = args[0]
    if len(args) > 1:
        email = args[1]
    if len(args) > 2:
        phone = args[2]
    if len(args) > 3:
        telegram = args[3]
        
    if event_type == "message":
        send_mock_message_webhook(name, email, phone, telegram)
    else:
        send_mock_chat_webhook(name, email, phone, telegram)
