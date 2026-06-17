import httpx
import hmac
import hashlib
import logging

logger = logging.getLogger("bridge")

def _get_headers(api_key: str) -> dict:
    """Helper to generate HelpCrunch Bearer Authorization header."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

async def test_connection(api_key: str) -> bool:
    """Tests connection to HelpCrunch by fetching customers with limit 1."""
    url = "https://api.helpcrunch.com/v1/customers"
    params = {"limit": 1}
    headers = _get_headers(api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=10.0)
            if response.status_code == 200:
                return True
            logger.warning(f"HelpCrunch test connection failed: Status {response.status_code}, Body {response.text}")
            return False
    except Exception as e:
        logger.exception("HelpCrunch connection error:")
        return False

async def get_customer(api_key: str, customer_id: int) -> dict:
    """Retrieves full customer profile details from HelpCrunch."""
    url = f"https://api.helpcrunch.com/v1/customers/{customer_id}"
    headers = _get_headers(api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                return response.json()
            logger.warning(f"Failed to fetch HelpCrunch customer {customer_id}: {response.text}")
            return {}
    except Exception as e:
        logger.exception(f"HelpCrunch get customer error for {customer_id}:")
        return {}

async def update_customer_notes(api_key: str, customer_id: int, notes: str) -> bool:
    """Updates the customer's notes in HelpCrunch."""
    url = f"https://api.helpcrunch.com/v1/customers/{customer_id}"
    headers = _get_headers(api_key)
    payload = {"notes": notes}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return True
            logger.warning(f"Failed to update HelpCrunch customer notes for {customer_id}: Status {response.status_code}, Body {response.text}")
            return False
    except Exception as e:
        logger.exception(f"HelpCrunch update customer notes error for {customer_id}:")
        return False

async def add_private_note(api_key: str, chat_id: int, text: str) -> bool:
    """Adds a private note to a chat conversation in HelpCrunch."""
    url = "https://api.helpcrunch.com/v1/messages"
    headers = _get_headers(api_key)
    payload = {
        "chat": chat_id,
        "text": text,
        "type": "private"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return True
            logger.warning(f"Failed to add HelpCrunch private note: Status {response.status_code}, Body {response.text}")
            return False
    except Exception as e:
        logger.exception(f"HelpCrunch add private note error in chat {chat_id}:")
        return False

def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """
    Verifies that the webhook payload is signed with the correct secret key.
    HelpCrunch uses HMAC-SHA1 signature sent in the X-HelpCrunch-Signature header.
    """
    if not secret:
        # If no secret is configured, bypass signature check (helpful for debugging/dev)
        return True
        
    if not signature:
        return False
        
    try:
        # Calculate signature
        computed_sig = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha1
        ).hexdigest()
        
        # Compare securely
        return hmac.compare_digest(computed_sig, signature)
    except Exception as e:
        logger.exception("Error verifying signature:")
        return False

async def update_customer(api_key: str, customer_id: int, payload: dict) -> bool:
    """
    Updates general customer details in HelpCrunch (e.g. email, phone, customData).
    """
    url = f"https://api.helpcrunch.com/v1/customers/{customer_id}"
    headers = _get_headers(api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return True
            logger.warning(f"Failed to update HelpCrunch customer {customer_id}: Status {response.status_code}, Body {response.text}")
            return False
    except Exception as e:
        logger.exception(f"HelpCrunch update customer error for {customer_id}:")
        return False
