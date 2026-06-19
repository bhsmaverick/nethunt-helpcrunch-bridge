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
    # Construct eventData
    payload = {
        "event": "chat.new",
        "eventData": {
            "id": chat_id,
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
                ]
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

if __name__ == "__main__":
    name = "John Doe"
    email = "john.doe@example.com"
    phone = "+380501112233"
    telegram = "johndoe_tg"
    
    if len(sys.argv) > 1:
        name = sys.argv[1]
    if len(sys.argv) > 2:
        email = sys.argv[2]
    if len(sys.argv) > 3:
        phone = sys.argv[3]
    if len(sys.argv) > 4:
        telegram = sys.argv[4]
        
    send_mock_chat_webhook(name, email, phone, telegram)
