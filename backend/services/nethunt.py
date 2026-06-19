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
        
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/searches/find-record/{folder_id}"
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
        
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/searches/find-record/{deals_folder_id}"
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

async def create_contact(email: str, api_key: str, base_url: str, folder_id: str, fields: dict) -> tuple:
    """
    Creates a new contact record in NetHunt CRM.
    Returns (record_dict, error_message). On success error_message is None.
    """
    if not folder_id or not fields:
        return None, "Missing folder_id or fields for create_contact"

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
                result = response.json()
                # NetHunt's Zapier endpoint returns the record ID under "recordId".
                # Normalize it so downstream code can always use the "id" key.
                if isinstance(result, dict) and "recordId" in result and "id" not in result:
                    result["id"] = result["recordId"]
                return result, None
            error_msg = f"Failed to create NetHunt contact: Status {response.status_code}, Body {response.text}"
            logger.error(error_msg)
            return None, error_msg
    except Exception as e:
        logger.exception("NetHunt create contact error:")
        return None, f"NetHunt create contact error: {e}"

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


# --- Bulk / sync endpoints for local mirror ---

async def find_records(email: str, api_key: str, base_url: str, folder_id: str, query: str = "", limit: int = 1000, offset: int = 0) -> list:
    """
    Searches for records in a NetHunt folder.
    Handles list/dict/wrapped response shapes and normalizes record IDs.
    """
    if not folder_id:
        return []

    url = f"{_clean_base_url(base_url)}/api/v1/zapier/searches/find-record/{folder_id}"
    params = {"query": query, "limit": limit, "offset": offset}
    headers = _get_auth_headers(email, api_key)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=30.0)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict) and "data" in data:
                    items = data["data"]
                else:
                    items = []
                # Normalize IDs so every record has an "id" key
                for item in items:
                    if isinstance(item, dict) and "recordId" in item and "id" not in item:
                        item["id"] = item["recordId"]
                return items
            elif response.status_code == 404:
                return []
            logger.warning(f"NetHunt find_records status {response.status_code} for folder '{folder_id}': {response.text}")
            return []
    except Exception as e:
        logger.exception(f"NetHunt find_records error for folder '{folder_id}':")
        return []


async def list_all_records_since(email: str, api_key: str, base_url: str, folder_id: str, since: str, limit: int = 1000) -> list:
    """
    Lists records created/updated since a given timestamp using the NetHunt trigger endpoint.
    This is the reliable way to fetch all records for a folder (use since=1970-01-01 for full sync).
    """
    if not folder_id:
        return []

    url = f"{_clean_base_url(base_url)}/api/v1/zapier/triggers/new-record/{folder_id}"
    params = {"since": since, "limit": limit}
    headers = _get_auth_headers(email, api_key)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=60.0)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict) and "data" in data:
                    items = data["data"]
                else:
                    items = []
                for item in items:
                    if isinstance(item, dict) and "recordId" in item and "id" not in item:
                        item["id"] = item["recordId"]
                logger.info(f"NetHunt list_all_records_since returned {len(items)} records for folder '{folder_id}'")
                return items
            elif response.status_code == 404:
                logger.warning(f"NetHunt list_all_records_since 404 for folder '{folder_id}'")
                return []
            logger.warning(f"NetHunt list_all_records_since status {response.status_code} for folder '{folder_id}': {response.text}")
            return []
    except Exception as e:
        logger.exception(f"NetHunt list_all_records_since error for folder '{folder_id}':")
        return []


def _record_created_at(item: dict) -> str:
    """Extracts the record creation timestamp from a NetHunt record."""
    if not isinstance(item, dict):
        return ""
    return item.get("createdAt") or item.get("createdTime") or item.get("created_at") or ""


async def find_all_records(email: str, api_key: str, base_url: str, folder_id: str, query: str = "", page_size: int = 1000, max_pages: int = 100) -> list:
    """
    Fetches all records in a NetHunt folder.
    Uses the new-record trigger endpoint and paginates by the latest createdAt
    because NetHunt caps the response at 10,000 records per request.
    """
    if not folder_id:
        return []

    page_size = 10000
    max_pages = 20
    since = "1970-01-01T00:00:00.000Z"
    all_items = []
    seen_ids = set()

    for page in range(max_pages):
        items = await list_all_records_since(email, api_key, base_url, folder_id, since=since, limit=page_size)
        if not items:
            break

        new_items = []
        for item in items:
            record_id = item.get("id") or item.get("recordId")
            if record_id in seen_ids:
                continue
            seen_ids.add(record_id)
            new_items.append(item)

        if not new_items:
            break
        all_items.extend(new_items)

        if len(items) < page_size:
            break

        # Find the latest createdAt in the returned page to use as the next cursor.
        latest_ts = ""
        for item in items:
            ts = _record_created_at(item)
            if ts and ts > latest_ts:
                latest_ts = ts

        if not latest_ts:
            logger.warning(f"NetHunt find_all_records: could not determine latest createdAt for folder '{folder_id}', stopping pagination.")
            break

        since = latest_ts
        logger.info(f"NetHunt find_all_records page {page + 1}: {len(new_items)} new records, latest createdAt={since}, total={len(all_items)}")

    logger.info(f"NetHunt find_all_records finished for folder '{folder_id}': total={len(all_items)}")
    return all_items
