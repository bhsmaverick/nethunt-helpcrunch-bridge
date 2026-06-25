import httpx
import base64
import logging
import re

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

async def find_contact(email: str, api_key: str, base_url: str, folder_id: str, query: str,
                       expected_field: str = None, expected_value: str = None) -> dict:
    """
    Searches for a contact by a query string.
    The query can be a raw search term or NetHunt search query (e.g. `Email:"value"`).
    If expected_field and expected_value are provided, validates that the returned
    record actually contains that value in that field (prevents false matches).
    Returns the first matching record or None if no match is found.
    """
    if not folder_id or not query:
        return None
        
    url = f"{_clean_base_url(base_url)}/api/v1/zapier/searches/find-record/{folder_id}"
    # Use higher limit to scan multiple results if validation is needed
    limit = 20 if expected_field else 1
    params = {"query": query, "limit": limit}
    headers = _get_auth_headers(email, api_key)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                # Normalize so recordId maps to id for all downstream code
                if isinstance(data, dict) and "data" in data:
                    records = _normalize_records_response(data["data"])
                else:
                    records = _normalize_records_response(data)
                
                if not records:
                    return None
                
                # If validation requested, find the record that actually matches
                if expected_field and expected_value:
                    def _norm(s):
                        # Tolerant compare: drop @, spaces and phone punctuation
                        return re.sub(r"[\s@()+\-.]", "", str(s).strip().lower())
                    ev_norm = _norm(expected_value)
                    for rec in records:
                        fields = rec.get("fields", {}) or {}
                        raw = fields.get(expected_field)
                        if raw is None:
                            continue
                        vals = raw if isinstance(raw, list) else [raw]
                        for v in vals:
                            v_norm = _norm(v)
                            if v_norm and ev_norm and v_norm == ev_norm:
                                return rec
                    # No validated match found
                    logger.warning(f"NetHunt find_contact: query '{query}' returned {len(records)} records but none matched {expected_field}={expected_value!r}")
                    return None
                else:
                    return records[0]
            elif response.status_code == 404:
                # 404 is returned by NetHunt if no record matches the query
                return None
            logger.warning(f"NetHunt contact search status {response.status_code} for query '{query}': {response.text}")
            return None
    except Exception as e:
        logger.exception(f"NetHunt search contact error for query '{query}':")
        return None

async def get_contact(email: str, api_key: str, base_url: str, record_id: str, folder_id: str = None) -> dict:
    """
    Fetches a single NetHunt record by its ID.
    Returns the record dict or None if not found.
    Strictly matches by id or recordId — never returns a wrong record.
    """
    if not record_id:
        return None
    headers = _get_auth_headers(email, api_key)

    def _matches(item):
        return item.get("id") == record_id or item.get("recordId") == record_id

    # Try the find-record endpoint with folder_id and recordId param
    if folder_id:
        url = f"{_clean_base_url(base_url)}/api/v1/zapier/searches/find-record/{folder_id}"
        params = {"recordId": record_id, "limit": 1}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, params=params, timeout=10.0)
                if response.status_code == 200:
                    for item in _normalize_records_response(response.json()):
                        if _matches(item):
                            return item
        except Exception:
            logger.exception(f"NetHunt get_contact (find-record) error for record '{record_id}':")

    # Fallback: try direct record endpoint
    url = f"{_clean_base_url(base_url)}/api/v1/record/{record_id}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                for item in _normalize_records_response(response.json()):
                    if _matches(item):
                        return item
            logger.warning(f"NetHunt get_contact status {response.status_code} for record '{record_id}': {response.text}")
            return None
    except Exception:
        logger.exception(f"NetHunt get_contact error for record '{record_id}':")
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

async def update_contact(email: str, api_key: str, base_url: str, record_id: str, fields: dict, overwrite: bool = True) -> bool:
    """
    Updates fields on an existing NetHunt Contact.
    When overwrite=True, replaces existing values. When overwrite=False, appends to multi-value fields.
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
                "overwrite": overwrite,
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

def _normalize_records_response(data) -> list:
    """Normalizes NetHunt list/dict/wrapped response shapes."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("data"), list):
        items = data["data"]
    elif isinstance(data, dict) and isinstance(data.get("records"), list):
        items = data["records"]
    elif isinstance(data, dict) and ("id" in data or "recordId" in data or "fields" in data):
        # A single bare record dict (not wrapped in data/records)
        items = [data]
    else:
        items = []

    for item in items:
        if isinstance(item, dict) and "recordId" in item and "id" not in item:
            item["id"] = item["recordId"]

    return items


async def find_records(email: str, api_key: str, base_url: str, folder_id: str, query: str = "", limit: int = 1000, offset: int = 0) -> list:
    """
    Searches for records in a NetHunt folder.
    Note: NetHunt appears to ignore offset/page for this endpoint.
    """
    if not folder_id:
        return []

    url = f"{_clean_base_url(base_url)}/api/v1/zapier/searches/find-record/{folder_id}"
    params = {"query": query, "limit": limit, "offset": offset}
    headers = _get_auth_headers(email, api_key)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=300.0)

            if response.status_code == 200:
                return _normalize_records_response(response.json())

            elif response.status_code == 404:
                return []

            logger.warning(f"NetHunt find_records status {response.status_code} for folder '{folder_id}', query={query!r}: {response.text[:500]}")
            return []

    except Exception:
        logger.exception(f"NetHunt find_records error for folder '{folder_id}', query={query!r}:")
        return []


async def list_all_records_since(email: str, api_key: str, base_url: str, folder_id: str, since: str, limit: int = 1000) -> list:
    """
    Lists records created/updated since a given timestamp using NetHunt new-record trigger.
    Important: this endpoint returns newest -> oldest and does not support offset/page.
    """
    if not folder_id:
        return []

    url = f"{_clean_base_url(base_url)}/api/v1/zapier/triggers/new-record/{folder_id}"
    params = {"since": since, "limit": limit}
    headers = _get_auth_headers(email, api_key)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=300.0)

            if response.status_code == 200:
                items = _normalize_records_response(response.json())
                logger.info(
                    f"NetHunt list_all_records_since returned {len(items)} records "
                    f"for folder '{folder_id}', since={since}, limit={limit}"
                )
                return items

            elif response.status_code == 404:
                logger.warning(f"NetHunt list_all_records_since 404 for folder '{folder_id}'")
                return []

            logger.warning(f"NetHunt list_all_records_since status {response.status_code} for folder '{folder_id}': {response.text[:500]}")
            return []

    except Exception:
        logger.exception(f"NetHunt list_all_records_since error for folder '{folder_id}':")
        return []


async def search_records_by_query(email: str, api_key: str, base_url: str, folder_id: str, query: str, limit: int = 50000) -> list:
    """
    Searches NetHunt records by broad query using find-record.
    This endpoint supports high limit, but does not support offset/page.
    """
    if not folder_id or not query:
        return []

    url = f"{_clean_base_url(base_url)}/api/v1/zapier/searches/find-record/{folder_id}"
    params = {"query": query, "limit": limit}
    headers = _get_auth_headers(email, api_key)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=300.0)

            if response.status_code == 200:
                items = _normalize_records_response(response.json())
                logger.info(
                    f"NetHunt search_records_by_query returned {len(items)} records "
                    f"for folder '{folder_id}', query={query!r}, limit={limit}"
                )
                return items

            logger.warning(
                f"NetHunt search_records_by_query status {response.status_code} "
                f"for folder '{folder_id}', query={query!r}: {response.text[:500]}"
            )
            return []

    except Exception:
        logger.exception(f"NetHunt search_records_by_query error for folder '{folder_id}', query={query!r}:")
        return []


def _record_created_at(item: dict) -> str:
    """Extracts the record creation timestamp from a NetHunt record."""
    if not isinstance(item, dict):
        return ""
    return item.get("createdAt") or item.get("createdTime") or item.get("created_at") or ""


async def find_all_records(email: str, api_key: str, base_url: str, folder_id: str, query: str = "", page_size: int = 1000, max_pages: int = 100) -> list:
    """
    Fetches as many records as NetHunt API allows.

    Strategy:
    1. Use new-record with limit=10000 for latest records.
    2. If less than 10000 returned, folder is likely fully fetched.
    3. If exactly 10000 returned, use broad find-record queries with limit=50000
       and deduplicate by recordId.
    """
    if not folder_id:
        return []

    all_items_by_id = {}

    def add_items(items: list, source: str) -> int:
        added = 0

        for item in items:
            if not isinstance(item, dict):
                continue

            record_id = item.get("id") or item.get("recordId")
            if not record_id:
                continue

            if "recordId" in item and "id" not in item:
                item["id"] = item["recordId"]

            if record_id not in all_items_by_id:
                all_items_by_id[record_id] = item
                added += 1

        logger.info(
            f"NetHunt find_all_records source={source}: "
            f"items={len(items)}, new={added}, total={len(all_items_by_id)}"
        )
        return added

    latest_items = await list_all_records_since(
        email,
        api_key,
        base_url,
        folder_id,
        since="1970-01-01T00:00:00.000Z",
        limit=10000,
    )
    add_items(latest_items, "new-record")

    # If NetHunt returned less than cap, we likely already have the whole folder.
    # This keeps deals fast: current deals are ~5k, so no broad search needed there.
    if len(latest_items) < 10000:
        all_items = list(all_items_by_id.values())
        logger.info(f"NetHunt find_all_records finished for folder '{folder_id}': total={len(all_items)}")
        return all_items

    broad_queries = [
        "a",
        "@",
        "+380",
        "+38",
        "+48",
        "+49",
        "+1",
        "+44",
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
    ]

    if query and query not in broad_queries:
        broad_queries.insert(0, query)

    for q in broad_queries:
        items = await search_records_by_query(
            email,
            api_key,
            base_url,
            folder_id,
            query=q,
            limit=50000,
        )
        add_items(items, f"find-record:{q}")

    all_items = list(all_items_by_id.values())
    logger.info(f"NetHunt find_all_records finished for folder '{folder_id}': total={len(all_items)}")
    return all_items
