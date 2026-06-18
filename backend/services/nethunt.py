import httpx
import base64
import logging

logger = logging.getLogger("bridge")

def _get_auth_headers(email: str, api_key: str) -> dict:
    """Helper to generate NetHunt Basic Authentication header."""
    auth_str = f"{email}:{api_key}"
    auth_bytes = auth_str.encode("utf-8")
    b64_auth = base64.b64encode(auth_bytes).decode("utf-8")
    return {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/json"
    }

def _clean_base_url(url: str) -> str:
    """Ensures base URL ends without trailing slash."""
    url = url.strip()
    if url.endswith("/"):
        url = url[:-1]
    return url

async def test_connection(email: str, api_key: str, base_url: str) -> bool:
    """Tests connection to NetHunt by listing folders."""
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/triggers/readable-folder"
    headers = _get_auth_headers(email, api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                return True
            logger.warning(f"NetHunt test connection failed: Status {response.status_code}, Body {response.text}")
            return False
    except Exception as e:
        logger.exception("NetHunt connection error:")
        return False

async def list_folders(email: str, api_key: str, base_url: str) -> list:
    """Retrieves a list of folders (id and name) from NetHunt CRM."""
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/triggers/readable-folder"
    headers = _get_auth_headers(email, api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                return response.json()
            logger.error(f"Failed to list NetHunt folders: {response.text}")
            return []
    except Exception as e:
        logger.exception("NetHunt list folders error:")
        return []

async def find_contact(email: str, api_key: str, base_url: str, folder_id: str, query: str) -> dict:
    """
    Searches for a contact by a query string.
    The query can be a raw search term or NetHunt search query (e.g. `Email:"value"`).
    Returns the first matching record or None if no match is found.
    """
    if not folder_id or not query:
        return None
        
    url = f"{_clean_base_url(base_url)}/api/v1/searches/find-record/{folder_id}"
    params = {"query": query, "limit": 1}
    headers = _get_auth_headers(email, api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
                elif isinstance(data, dict) and "id" in data:
                    return data
                elif isinstance(data, dict) and "data" in data and len(data["data"]) > 0:
                    return data["data"][0]
            elif response.status_code == 404:
                # 404 is returned by NetHunt if no record matches the query
                return None
            logger.warning(f"NetHunt contact search status {response.status_code} for query '{query}': {response.text}")
            return None
    except Exception as e:
        logger.exception(f"NetHunt search contact error for query '{query}':")
        return None

async def find_deals(email: str, api_key: str, base_url: str, deals_folder_id: str, contact_record_id: str) -> list:
    """
    Retrieves deals related to a contact record ID.
    Performs a query search in the deals folder for the contact ID.
    """
    if not deals_folder_id or not contact_record_id:
        return []
        
    url = f"{_clean_base_url(base_url)}/api/v1/searches/find-record/{deals_folder_id}"
    params = {"query": contact_record_id, "limit": 10}
    headers = _get_auth_headers(email, api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "data" in data:
                    return data["data"]
            logger.warning(f"NetHunt deals search status {response.status_code} for contact '{contact_record_id}': {response.text}")
            return []
    except Exception as e:
        logger.exception(f"NetHunt search deals error for contact '{contact_record_id}':")
        return []

async def update_contact_chat_link(email: str, api_key: str, base_url: str, record_id: str, field_name: str, chat_link: str) -> bool:
    """
    Updates the HelpCrunch Chat Link field of a NetHunt record.
    """
    if not record_id or not field_name or not chat_link:
        return False
        
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/actions/update-record/{record_id}"
    headers = _get_auth_headers(email, api_key)
    payload = {
        "fieldActions": {
            field_name: {
                "overwrite": True,
                "add": chat_link
            }
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return True
            logger.warning(f"Failed to update NetHunt contact chat link: Status {response.status_code}, Body {response.text}")
            return False
    except Exception as e:
        logger.exception(f"NetHunt update contact chat link error for record '{record_id}':")
        return False

async def update_contact(email: str, api_key: str, base_url: str, record_id: str, fields: dict) -> bool:
    """
    Updates fields on an existing NetHunt Contact.
    """
    if not record_id or not fields:
        return False
        
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/actions/update-record/{record_id}"
    headers = _get_auth_headers(email, api_key)
    
    # Map fields to NetHunt fieldActions structure
    field_actions = {}
    for key, val in fields.items():
        if val is not None and val != "":
            field_actions[key] = {
                "overwrite": True,
                "add": val
            }
            
    payload = {"fieldActions": field_actions}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return True
            logger.warning(f"Failed to update NetHunt contact fields: Status {response.status_code}, Body {response.text}")
            return False
    except Exception as e:
        logger.exception(f"NetHunt update contact error for record '{record_id}':")
        return False

async def create_contact(email: str, api_key: str, base_url: str, folder_id: str, fields: dict) -> dict:
    """
    Creates a new contact record in NetHunt CRM.
    """
    if not folder_id or not fields:
        return None
        
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/actions/create-record/{folder_id}"
    headers = _get_auth_headers(email, api_key)
    payload = {
        "fields": fields,
        "timeZone": "Europe/Kiev"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code in (200, 201):
                return response.json()
            logger.error(f"Failed to create NetHunt contact: Status {response.status_code}, Body {response.text}")
            return None
    except Exception as e:
        logger.exception("NetHunt create contact error:")
        return None

async def list_folder_fields(email: str, api_key: str, base_url: str, folder_id: str) -> list:
    """
    Retrieves the list of fields (id and name) for a specific folder in NetHunt CRM.
    """
    if not folder_id:
        return []
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/triggers/folder-field/{folder_id}"
    headers = _get_auth_headers(email, api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                return response.json()
            logger.error(f"Failed to list NetHunt folder fields: {response.text}")
            return []
    except Exception as e:
        logger.exception(f"NetHunt list folder fields error for {folder_id}:")
        return []

