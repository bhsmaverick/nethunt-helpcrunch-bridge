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

async def update_customer_notes(api_key: str, customer_id: int, notes: str) -> tuple:
    """Updates the customer's notes in HelpCrunch. Returns (success, error_detail)."""
    url = f"https://api.helpcrunch.com/v1/customers/{customer_id}"
    headers = _get_headers(api_key)
    payload = {"notes": notes}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return True, None
            error_detail = f"Status {response.status_code}, Body {response.text}"
            logger.warning(f"Failed to update HelpCrunch customer notes for {customer_id}: {error_detail}")
            return False, error_detail
    except Exception as e:
        error_detail = str(e)
        logger.exception(f"HelpCrunch update customer notes error for {customer_id}:")
        return False, error_detail

async def add_private_note(api_key: str, chat_id: int, text: str, markdown_text: str = None) -> tuple:
    """Adds a private note to a chat conversation in HelpCrunch. Returns (success, error_detail)."""
    url = "https://api.helpcrunch.com/v1/messages"
    headers = _get_headers(api_key)
    payload = {
        "chat": chat_id,
        "text": text,
        "type": "private"
    }
    if markdown_text:
        payload["markdownText"] = markdown_text
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return True, None
            error_detail = f"Status {response.status_code}, Body {response.text}"
            logger.warning(f"Failed to add HelpCrunch private note: {error_detail}")
            return False, error_detail
    except Exception as e:
        error_detail = str(e)
        logger.exception(f"HelpCrunch add private note error in chat {chat_id}:")
        return False, error_detail

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

async def update_customer(api_key: str, customer_id: int, payload: dict) -> tuple:
    """
    Updates general customer details in HelpCrunch (e.g. email, phone, customData).
    Returns (success, error_detail).
    """
    url = f"https://api.helpcrunch.com/v1/customers/{customer_id}"
    headers = _get_headers(api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return True, None
            error_detail = f"Status {response.status_code}, Body {response.text}"
            logger.warning(f"Failed to update HelpCrunch customer {customer_id}: {error_detail}")
            return False, error_detail
    except Exception as e:
        error_detail = str(e)
        logger.exception(f"HelpCrunch update customer error for {customer_id}:")
        return False, error_detail


# --- Bulk / sync endpoints for local mirror ---

async def list_customers(api_key: str, limit: int = 100, offset: int = 0) -> dict:
    """Fetches a page of HelpCrunch customers. Returns the raw API response dict."""
    url = "https://api.helpcrunch.com/v1/customers"
    headers = _get_headers(api_key)
    params = {"limit": limit, "offset": offset}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=30.0)
            if response.status_code == 200:
                return response.json()
            logger.warning(f"Failed to list HelpCrunch customers: Status {response.status_code}, Body {response.text}")
            return {}
    except Exception as e:
        logger.exception("HelpCrunch list customers error:")
        return {}

async def list_all_customers(api_key: str, page_size: int = 100, max_pages: int = 1000) -> list:
    """Paginates through all HelpCrunch customers and returns a flat list."""
    all_items = []
    offset = 0
    for page in range(max_pages):
        data = await list_customers(api_key, limit=page_size, offset=offset)
        if not data:
            break
        items = data if isinstance(data, list) else data.get("data", [])
        if not items:
            break
        all_items.extend(items)
        if len(items) < page_size:
            break
        offset += page_size
    return all_items

async def list_chats(api_key: str, limit: int = 100, offset: int = 0) -> dict:
    """Fetches a page of HelpCrunch chats. Returns the raw API response dict."""
    url = "https://api.helpcrunch.com/v1/chats"
    headers = _get_headers(api_key)
    params = {"limit": limit, "offset": offset}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=30.0)
            if response.status_code == 200:
                return response.json()
            logger.warning(f"Failed to list HelpCrunch chats: Status {response.status_code}, Body {response.text}")
            return {}
    except Exception as e:
        logger.exception("HelpCrunch list chats error:")
        return {}

async def list_all_chats(api_key: str, page_size: int = 100, max_pages: int = 1000) -> list:
    """Paginates through all HelpCrunch chats and returns a flat list."""
    all_items = []
    offset = 0
    for page in range(max_pages):
        data = await list_chats(api_key, limit=page_size, offset=offset)
        if not data:
            break
        items = data if isinstance(data, list) else data.get("data", [])
        if not items:
            break
        all_items.extend(items)
        if len(items) < page_size:
            break
        offset += page_size
    return all_items
