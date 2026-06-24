import logging
import json
import re
import asyncio
import traceback
from urllib.parse import urlparse
from typing import Optional

from ..database import (
    get_settings, add_log, get_db_connection,
    find_hc_chats_by_customer_id,
)
from ..database import find_nh_contact_by_chat_link, find_match_by_hc_customer_id, get_nh_contact_by_id
from ..services import nethunt, helpcrunch
from ..extractors import (
    extract_email, extract_phone, extract_messengers,
    extract_params_from_url, detect_platform_from_url,
    build_chat_link, build_nethunt_record_url, extract_name,
)
from .. import sync_engine

logger = logging.getLogger("bridge")

# Per-customer locks to prevent concurrent webhook processing from creating duplicates
_customer_locks: dict = {}
_customer_locks_guard = asyncio.Lock()
_CUSTOMER_LOCKS_MAX = 500


async def _get_customer_lock(customer_id):
    async with _customer_locks_guard:
        if len(_customer_locks) > _CUSTOMER_LOCKS_MAX:
            to_remove = [k for k, v in _customer_locks.items() if not v.locked()]
            for k in to_remove:
                del _customer_locks[k]
        if customer_id not in _customer_locks:
            _customer_locks[customer_id] = asyncio.Lock()
        return _customer_locks[customer_id]


def _parse_custom_data(custom_data, telegram_hc_key):
    """Extract telegram, instagram, and UTM values from customData (list or dict)."""
    result = {
        "telegram_handle": "", "instagram_handle": "",
        "utm_source": "", "utm_medium": "", "utm_campaign": "",
        "utm_term": "", "utm_content": "", "gclid": "",
    }
    if not custom_data:
        return result
    if isinstance(custom_data, list):
        for item in custom_data:
            if isinstance(item, dict):
                prop = item.get("property") or item.get("name")
                val = item.get("value") or ""
                if prop == telegram_hc_key:
                    result["telegram_handle"] = val
                elif prop == "instagram":
                    result["instagram_handle"] = val
                elif prop == "utm_source":
                    result["utm_source"] = val
                elif prop == "utm_medium":
                    result["utm_medium"] = val
                elif prop == "utm_campaign":
                    result["utm_campaign"] = val
                elif prop == "utm_term":
                    result["utm_term"] = val
                elif prop == "utm_content":
                    result["utm_content"] = val
                elif prop == "gclid":
                    result["gclid"] = val
    elif isinstance(custom_data, dict):
        result["telegram_handle"] = custom_data.get(telegram_hc_key) or ""
        result["instagram_handle"] = custom_data.get("instagram") or ""
        result["utm_source"] = custom_data.get("utm_source") or ""
        result["utm_medium"] = custom_data.get("utm_medium") or ""
        result["utm_campaign"] = custom_data.get("utm_campaign") or ""
        result["utm_term"] = custom_data.get("utm_term") or ""
        result["utm_content"] = custom_data.get("utm_content") or ""
        result["gclid"] = custom_data.get("gclid") or ""
    return result


def _detect_messenger_name(cust_name, cust_created_from, telegram_handle, instagram_handle, customer_data):
    """Try to extract a name from messenger handles or customData."""
    needs_name = (not cust_name or cust_name == "Unknown Customer" or cust_name.strip() == "")
    if not needs_name or cust_created_from not in ("telegram", "instagram", "instagram_direct", "telegram_bot"):
        return None

    handle = telegram_handle or instagram_handle or ""
    if handle:
        parts = re.split(r'[_\.\-]+', handle)
        name_parts = [p.capitalize() for p in parts if p.isalpha() and len(p) >= 2]
        if name_parts:
            return " ".join(name_parts[:3])

    cd = customer_data.get("customData") or []
    name_keys = {"name", "first_name", "first name", "display_name", "display name",
                 "full_name", "full name", "last_name", "last name"}
    if isinstance(cd, list):
        for item in cd:
            if isinstance(item, dict):
                prop = (item.get("property") or item.get("name") or "").lower()
                val = item.get("value") or ""
                if prop in name_keys and val:
                    return val
    elif isinstance(cd, dict):
        for key in ("name", "first_name", "display_name", "full_name", "last_name"):
            val = cd.get(key)
            if val:
                return val
    return None


def _detect_handles_from_urls(cust_source, cust_referer, telegram_handle, instagram_handle, detected_platform):
    """Detect Telegram/Instagram handles from source/referer URLs."""
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

    return telegram_handle, instagram_handle, detected_platform


def _build_tracking_fields(utm_src_f, utm_med_f, utm_cam_f, utm_trm_f, utm_cnt_f,
                           gclid_f, referer_f, source_f, country_f, city_f,
                           branch_f, branch_mapping_str,
                           utm_source, utm_medium, utm_campaign, utm_term, utm_content,
                           gclid, cust_referer, cust_source, detected_platform,
                           cust_country, cust_city, details_log):
    """Build the tracking fields payload for NetHunt CRM."""
    tracking_fields = {}
    if utm_src_f and utm_source: tracking_fields[utm_src_f] = utm_source
    if utm_med_f and utm_medium: tracking_fields[utm_med_f] = utm_medium
    if utm_cam_f and utm_campaign: tracking_fields[utm_cam_f] = utm_campaign
    if utm_trm_f and utm_term: tracking_fields[utm_trm_f] = utm_term
    if utm_cnt_f and utm_content: tracking_fields[utm_cnt_f] = utm_content
    if gclid_f and gclid: tracking_fields[gclid_f] = gclid
    if referer_f and cust_referer: tracking_fields[referer_f] = cust_referer
    if source_f:
        tracking_fields[source_f] = detected_platform if detected_platform else (cust_source or "Organic/Direct")
    if country_f and cust_country: tracking_fields[country_f] = cust_country
    if city_f and cust_city: tracking_fields[city_f] = cust_city

    if branch_f and branch_mapping_str:
        try:
            branch_map = json.loads(branch_mapping_str)
            if isinstance(branch_map, dict):
                combined_urls = f"{cust_source} {cust_referer}".lower()
                for keyword, branch_value in branch_map.items():
                    if keyword.lower() in combined_urls:
                        tracking_fields[branch_f] = branch_value
                        details_log.append(f"Branch detected: '{branch_value}' (matched keyword '{keyword}' in source/referer)")
                        break
        except Exception:
            logger.warning(f"Failed to parse branch_mapping setting: {branch_mapping_str}")

    return tracking_fields


async def _search_local_mirror(chat_url, customer_id, details_log):
    """Search local mirror for an existing NetHunt contact. Returns (contact, search_method)."""
    contact = None
    search_method = ""

    # 1a. Match by chat_link
    if chat_url:
        try:
            local_contact = find_nh_contact_by_chat_link(chat_url)
            if local_contact and local_contact.get("raw_json"):
                contact = json.loads(local_contact["raw_json"])
                search_method = "Local Mirror (chat_link)"
                details_log.append(f"Matched existing NetHunt contact via local mirror chat_link: ID={local_contact.get('nh_record_id')}")
        except Exception:
            logger.exception("Local mirror chat_link lookup failed:")

    # 1b. Check user's chat history
    if not contact and customer_id:
        try:
            user_chats = find_hc_chats_by_customer_id(customer_id)
            for uc in user_chats:
                uc_chat_link = uc.get("chat_link") or ""
                if uc_chat_link:
                    local_contact = find_nh_contact_by_chat_link(uc_chat_link)
                    if local_contact and local_contact.get("raw_json"):
                        contact = json.loads(local_contact["raw_json"])
                        search_method = "Local Mirror (user chat history)"
                        details_log.append(f"Matched existing NetHunt contact via user's chat history (chat_link={uc_chat_link}): ID={local_contact.get('nh_record_id')}")
                        break
        except Exception:
            logger.exception("Local mirror user chat history lookup failed:")

    # 1c. Match by HC customer ID
    if not contact and customer_id:
        try:
            match = find_match_by_hc_customer_id(customer_id)
            if match:
                local_contact = get_nh_contact_by_id(match["nh_contact_id"])
                if local_contact and local_contact.get("raw_json"):
                    contact = json.loads(local_contact["raw_json"])
                    search_method = "Local Mirror (HC customer match)"
                    details_log.append(f"Matched existing NetHunt contact via HC customer match: ID={local_contact.get('nh_record_id')}")
        except Exception:
            logger.exception("Local mirror HC customer match failed:")

    # 1d. Field-based match
    if not contact:
        try:
            local_contact = await sync_engine.resolve_nh_contact({"id": customer_id}, None)
            if local_contact and local_contact.get("raw_json"):
                contact = json.loads(local_contact["raw_json"])
                search_method = "Local Mirror (field match)"
                details_log.append(f"Matched existing NetHunt contact via local mirror fields: ID={local_contact.get('nh_record_id')}")
        except Exception:
            logger.exception("Local mirror field-based resolution failed:")

    return contact, search_method


async def _search_nethunt_api(customer_id, merged_email, merged_phone, merged_telegram,
                              hc_id_nh_key, email_nh_key, phone_nh_key, telegram_nh_key,
                              priority_str, nh_email, nh_key, nh_base, contacts_folder,
                              details_log):
    """Search NetHunt API directly. Returns (contact, search_method)."""
    contact = None
    search_method = ""

    if hc_id_nh_key and customer_id:
        details_log.append(f"Searching NetHunt by HelpCrunch ID: '{customer_id}' (Field: '{hc_id_nh_key}')...")
        query_str = f'"{hc_id_nh_key}":"{customer_id}"'
        contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
        if contact:
            search_method = "HelpCrunch ID"

    if not contact:
        priorities = [p.strip() for p in priority_str.split(",") if p.strip()]
        for step in priorities:
            if step == "email" and merged_email and email_nh_key:
                details_log.append(f"Searching NetHunt by Email: '{merged_email}' (Field: '{email_nh_key}')...")
                query_str = f'"{email_nh_key}":"{merged_email}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method = "Email"
                    break
            elif step == "phone" and merged_phone and phone_nh_key:
                details_log.append(f"Searching NetHunt by Phone: '{merged_phone}' (Field: '{phone_nh_key}')...")
                query_str = f'"{phone_nh_key}":"{merged_phone}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method = "Phone"
                    break
            elif step == "telegram" and merged_telegram and telegram_nh_key:
                details_log.append(f"Searching NetHunt by Telegram: '{merged_telegram}' (Field: '{telegram_nh_key}')...")
                query_str = f'"{telegram_nh_key}":"{merged_telegram}"'
                contact = await nethunt.find_contact(nh_email, nh_key, nh_base, contacts_folder, query_str)
                if contact:
                    search_method = "Telegram"
                    break

    return contact, search_method


async def _update_existing_contact(contact, customer_id, merged_email, merged_phone, merged_telegram,
                                   merged_instagram, tracking_fields, settings,
                                   hc_id_nh_key, email_nh_key, phone_nh_key, telegram_nh_key, instagram_nh_key,
                                   nh_email, nh_key, nh_base, details_log, telegram_id=""):
    """Update an existing NetHunt contact with missing/append fields."""
    contact_id = contact.get("id")
    contact_fields = contact.get("fields", {})

    def _existing_values(field_key):
        raw = contact_fields.get(field_key)
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(v).strip().lower() for v in raw if v]
        return [str(raw).strip().lower()]

    def _value_exists(field_key, new_val):
        return str(new_val).strip().lower() in _existing_values(field_key)

    overwrite_fields = {}
    append_fields = {}
    notes_additions = []

    if customer_id and hc_id_nh_key and not contact_fields.get(hc_id_nh_key):
        overwrite_fields[hc_id_nh_key] = str(customer_id)
        details_log.append(f"Linking HelpCrunch ID to NetHunt contact: '{customer_id}'")

    # Build list of fields to update; if no telegram handle but have ID, use ID
    tg_val = merged_telegram or (telegram_id if not contact_fields.get(telegram_nh_key) else "")

    for label, val, field_key in [
        ("Email", merged_email, email_nh_key),
        ("Phone", merged_phone, phone_nh_key),
        ("Telegram handle", tg_val, telegram_nh_key),
        ("Instagram handle", merged_instagram, instagram_nh_key),
    ]:
        if val and field_key:
            if not contact_fields.get(field_key):
                overwrite_fields[field_key] = val if field_key in (telegram_nh_key, instagram_nh_key) else [val]
                details_log.append(f"Adding missing {label} to NetHunt contact: '{val}'")
            elif not _value_exists(field_key, val):
                append_fields[field_key] = val if field_key in (telegram_nh_key, instagram_nh_key) else [val]
                details_log.append(f"Appending new {label} to NetHunt contact: '{val}'")

    for k, v in tracking_fields.items():
        if not contact_fields.get(k):
            overwrite_fields[k] = v

    if overwrite_fields:
        details_log.append(f"Updating NetHunt CRM contact fields (overwrite): {list(overwrite_fields.keys())}...")
        updated = await nethunt.update_contact(nh_email, nh_key, nh_base, contact_id, overwrite_fields, overwrite=True)
        if updated:
            details_log.append("NetHunt contact updated successfully.")
        else:
            details_log.append("Warning: Could not update NetHunt contact fields.")

    if append_fields:
        details_log.append(f"Appending to NetHunt CRM contact fields: {list(append_fields.keys())}...")
        appended = await nethunt.update_contact(nh_email, nh_key, nh_base, contact_id, append_fields, overwrite=False)
        if appended:
            details_log.append("New values appended to NetHunt contact successfully.")
        else:
            details_log.append("Append failed (field may not support multi-value). Writing to notes instead.")
            for k, v in append_fields.items():
                if isinstance(v, list):
                    notes_additions.append(f"{k}: {', '.join(v)}")
                else:
                    notes_additions.append(f"{k}: {v}")

    if notes_additions:
        notes_text = " | ".join(notes_additions)
        notes_field_key = settings.get("nethunt_notes_field_nh", "Additional Info")
        notes_updated = await nethunt.update_contact(nh_email, nh_key, nh_base, contact_id, {notes_field_key: notes_text}, overwrite=False)
        if notes_updated:
            details_log.append(f"Additional info written to NetHunt field '{notes_field_key}'.")
        else:
            details_log.append(f"Warning: Could not write additional info to NetHunt field '{notes_field_key}'.")


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
    nh_base = settings.get("nethunt_base_url", "https://nethunt.com")
    nh_workspace_id = settings.get("nethunt_workspace_id", "")
    contacts_folder = settings.get("nethunt_contacts_folder")
    deals_folder = settings.get("nethunt_deals_folder")
    priority_str = settings.get("sync_priority", "email,phone,telegram")
    telegram_hc_key = settings.get("telegram_field_hc", "telegram")
    telegram_nh_key = settings.get("telegram_field_nh", "Telegram")
    instagram_nh_key = settings.get("instagram_field_nh", "Instagram")
    name_nh_key = settings.get("name_field_nh", "Name")
    phone_nh_key = settings.get("phone_field_nh", "Phone")
    email_nh_key = settings.get("email_field_nh", "Email")
    hc_id_nh_key = settings.get("hc_id_field_nh", "HelpCrunch ID")
    update_nh_link = settings.get("update_nh_chat_link") == "true"
    nh_link_field = settings.get("nh_chat_link_field", "HelpCrunch Chat Link")
    hc_subdomain = settings.get("helpcrunch_subdomain", "")

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
    branch_f = settings.get("branch_field_nh", "")
    branch_mapping_str = settings.get("branch_mapping", "")

    # --- Parse customer data ---
    customer_id = customer_data.get("id")
    if customer_id is not None:
        try:
            customer_id = int(customer_id)
        except (ValueError, TypeError):
            pass
    cust_name = customer_data.get("name") or "Unknown Customer"
    cust_email = customer_data.get("email") or ""
    cust_phone = customer_data.get("phone") or ""
    cust_referer = customer_data.get("referer") or ""
    cust_source = customer_data.get("source") or ""
    cust_created_from = customer_data.get("createdFrom") or ""
    location_data = customer_data.get("location", {}) or {}
    cust_country = location_data.get("countryCode") or ""
    cust_city = location_data.get("city") or ""

    # Fetch full profile for message events
    if event_type == "message.chat.customer" and hc_api_key and customer_id:
        try:
            full_profile = await helpcrunch.get_customer(hc_api_key, customer_id)
            if full_profile and isinstance(full_profile, dict) and full_profile.get("id"):
                logger.info(f"Fetched full HC customer profile for message event: {full_profile.get('name', '')}")
                for k, v in full_profile.items():
                    if v is not None and v != "":
                        customer_data[k] = v
                cust_name = customer_data.get("name") or cust_name
                cust_email = customer_data.get("email") or ""
                cust_phone = customer_data.get("phone") or ""
                cust_referer = customer_data.get("referer") or ""
                cust_source = customer_data.get("source") or ""
                location_data = customer_data.get("location", {}) or {}
                cust_country = location_data.get("countryCode") or ""
                cust_city = location_data.get("city") or ""
        except Exception:
            logger.exception(f"Failed to fetch full HC customer profile for customer {customer_id}:")

    if cust_phone:
        normalized_initial_phone = extract_phone(cust_phone)
        if normalized_initial_phone:
            cust_phone = normalized_initial_phone

    # --- Parse customData ---
    cd_raw = customer_data.get("customData")
    cd_parsed = _parse_custom_data(cd_raw, telegram_hc_key)
    telegram_handle = cd_parsed["telegram_handle"]
    instagram_handle = cd_parsed["instagram_handle"]
    utm_source = cd_parsed["utm_source"]
    utm_medium = cd_parsed["utm_medium"]
    utm_campaign = cd_parsed["utm_campaign"]
    utm_term = cd_parsed["utm_term"]
    utm_content = cd_parsed["utm_content"]
    gclid = cd_parsed["gclid"]

    # --- Extended Telegram extraction ---
    # HelpCrunch may store telegram ID/username in various fields
    telegram_id = ""
    if not telegram_handle:
        # Try alternative customData keys for telegram username
        alt_tg_keys = ("telegram", "telegram_username", "telegramUsername",
                       "tg_username", "username", "Telegram", "Telegram Username",
                       "telegram_user", "tg", "Telegram username", "telegram_user_id")
        if isinstance(cd_raw, list):
            for item in cd_raw:
                if isinstance(item, dict):
                    prop = (item.get("property") or item.get("name") or "")
                    val = item.get("value") or ""
                    if prop and prop in alt_tg_keys and val:
                        telegram_handle = str(val).lstrip("@")
                        break
        elif isinstance(cd_raw, dict):
            for k in alt_tg_keys:
                val = cd_raw.get(k)
                if val:
                    telegram_handle = str(val).lstrip("@")
                    break

    # If still no handle and createdFrom is telegram, try using the customer name as username
    if not telegram_handle and cust_created_from in ("telegram", "telegram_bot"):
        # HelpCrunch sometimes stores the telegram username as the customer name
        if cust_name and cust_name != "Unknown Customer" and not cust_name.strip().isdigit():
            # Only use if it looks like a telegram handle (no spaces, starts with letter)
            if re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', cust_name.strip()):
                telegram_handle = cust_name.strip().lstrip("@")
                details_log_preview = f"Using customer name as Telegram username: '{telegram_handle}'"

    # Try userId as telegram ID (common for telegram bot integration)
    cust_user_id = str(customer_data.get("userId") or "")
    if cust_user_id and cust_created_from in ("telegram", "telegram_bot"):
        if cust_user_id.isdigit():
            telegram_id = cust_user_id

    # Also check customData for telegram ID
    alt_tg_id_keys = ("telegram_id", "telegramId", "Telegram ID", "tg_id", "telegram_user_id")
    if not telegram_id:
        if isinstance(cd_raw, list):
            for item in cd_raw:
                if isinstance(item, dict):
                    prop = (item.get("property") or item.get("name") or "")
                    val = item.get("value") or ""
                    if prop and prop in alt_tg_id_keys and val:
                        telegram_id = str(val)
                        break
        elif isinstance(cd_raw, dict):
            for k in alt_tg_id_keys:
                val = cd_raw.get(k)
                if val:
                    telegram_id = str(val)
                    break

    # --- Extended Instagram extraction ---
    if not instagram_handle:
        alt_ig_keys = ("instagram", "instagram_username", "instagramUsername",
                       "ig_username", "Instagram", "Instagram Username",
                       "instagram_user", "ig", "Instagram username")
        if isinstance(cd_raw, list):
            for item in cd_raw:
                if isinstance(item, dict):
                    prop = (item.get("property") or item.get("name") or "")
                    val = item.get("value") or ""
                    if prop and prop in alt_ig_keys and val:
                        instagram_handle = str(val).lstrip("@").rstrip("/")
                        break
        elif isinstance(cd_raw, dict):
            for k in alt_ig_keys:
                val = cd_raw.get(k)
                if val:
                    instagram_handle = str(val).lstrip("@").rstrip("/")
                    break

    # If still no handle and createdFrom is instagram, try using the customer name as username
    if not instagram_handle and cust_created_from in ("instagram", "instagram_direct"):
        if cust_name and cust_name != "Unknown Customer" and not cust_name.strip().isdigit():
            if re.match(r'^[a-zA-Z0-9_.]{1,30}$', cust_name.strip()) and " " not in cust_name.strip():
                instagram_handle = cust_name.strip().lstrip("@")

    # Log available customData keys for debugging
    if cd_raw:
        if isinstance(cd_raw, list):
            cd_keys = [item.get("property") or item.get("name") for item in cd_raw if isinstance(item, dict)]
        elif isinstance(cd_raw, dict):
            cd_keys = list(cd_raw.keys())
        else:
            cd_keys = []
        logger.info(f"HelpCrunch customData keys: {cd_keys}")
        logger.info(f"Telegram: handle='{telegram_handle}', id='{telegram_id}', userId='{cust_user_id}', createdFrom='{cust_created_from}'")
        logger.info(f"Instagram: handle='{instagram_handle}', createdFrom='{cust_created_from}'")

    # --- Parse UTM from URLs ---
    source_params = extract_params_from_url(cust_source)
    referer_params = extract_params_from_url(cust_referer)
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

    # --- Detect platform & handles from URLs ---
    detected_platform = detect_platform_from_url(cust_referer) or detect_platform_from_url(cust_source)
    telegram_handle, instagram_handle, detected_platform = _detect_handles_from_urls(
        cust_source, cust_referer, telegram_handle, instagram_handle, detected_platform
    )

    if not utm_medium and detected_platform:
        utm_medium = detected_platform
    if not utm_source and not utm_medium and not cust_source and not cust_referer and not gclid:
        utm_medium = "organic"

    # --- Extract info from message text ---
    extracted_email = None
    extracted_phone = None
    extracted_tg = None
    extracted_ig = None
    extracted_name = None

    if message_text:
        extracted_email = extract_email(message_text)
        extracted_phone = extract_phone(message_text)
        messengers = extract_messengers(message_text)
        extracted_tg = messengers.get("telegram")
        extracted_ig = messengers.get("instagram")
        extracted_name = extract_name(message_text, cust_phone or extracted_phone)

    # --- Clean handles ---
    if telegram_handle and telegram_handle.startswith("@"):
        telegram_handle = telegram_handle[1:]

    # --- Detect messenger name ---
    messenger_name = _detect_messenger_name(cust_name, cust_created_from, telegram_handle, instagram_handle, customer_data)

    # --- Merge details ---
    merged_email = cust_email or extracted_email or ""
    merged_phone = cust_phone or extracted_phone or ""
    merged_telegram = telegram_handle or extracted_tg or ""
    merged_instagram = instagram_handle or extracted_ig or ""

    # --- Logging ---
    details_log = []
    details_log.append(f"Starting processing for Event: {event_type}")
    details_log.append(f"Customer info: ID={customer_id}, Name='{cust_name}', Email='{cust_email}', Phone='{cust_phone}', Telegram='{telegram_handle}', TelegramID='{telegram_id}', Instagram='{instagram_handle}', createdFrom='{cust_created_from}'")
    if messenger_name:
        details_log.append(f"Detected messenger name: '{messenger_name}'")
    if message_text:
        details_log.append(f"Parsed Message Text: '{message_text}'")
        details_log.append(f"Extracted from message: Email='{extracted_email or ''}', Phone='{extracted_phone or ''}', Telegram='{extracted_tg or ''}', Instagram='{extracted_ig or ''}', Name='{extracted_name or ''}'")
    details_log.append(f"Merged fields: Email='{merged_email}', Phone='{merged_phone}', Telegram='{merged_telegram}', TelegramID='{telegram_id}', Instagram='{merged_instagram}'")
    details_log.append(f"Tracking: Source='{cust_source}', Referer='{cust_referer}', Country='{cust_country}', City='{cust_city}', Platform='{detected_platform}'")
    if utm_source or utm_medium or utm_campaign or gclid:
        details_log.append(f"UTMs: src='{utm_source}', med='{utm_medium}', cam='{utm_campaign}', gclid='{gclid}'")

    if not hc_api_key or not nh_email or not nh_key or not contacts_folder:
        err_msg = "Aborted: Credentials or folder mapping missing in Settings."
        details_log.append(err_msg)
        add_log(event_type, cust_name, merged_email, merged_phone, "error", "\n".join(details_log), level="error", hc_customer_id=customer_id)
        logger.error(err_msg)
        return

    # --- Build tracking fields ---
    tracking_fields = _build_tracking_fields(
        utm_src_f, utm_med_f, utm_cam_f, utm_trm_f, utm_cnt_f,
        gclid_f, referer_f, source_f, country_f, city_f,
        branch_f, branch_mapping_str,
        utm_source, utm_medium, utm_campaign, utm_term, utm_content,
        gclid, cust_referer, cust_source, detected_platform,
        cust_country, cust_city, details_log
    )

    # --- Build chat URL ---
    chat_url = ""
    if chat_id and hc_subdomain:
        chat_url = build_chat_link(hc_subdomain, chat_id)

    # --- STEP 1: Search local mirror ---
    contact, search_method_used = await _search_local_mirror(chat_url, customer_id, details_log)

    # --- STEP 2: Search NetHunt API ---
    if not contact:
        contact, search_method_used = await _search_nethunt_api(
            customer_id, merged_email, merged_phone, merged_telegram,
            hc_id_nh_key, email_nh_key, phone_nh_key, telegram_nh_key,
            priority_str, nh_email, nh_key, nh_base, contacts_folder,
            details_log
        )

    # --- STEP 3: Find or create contact ---
    is_new_contact = False
    if not contact:
        details_log.append("No matching contact found in NetHunt CRM. Creating a new contact card...")

        new_fields = {}
        if customer_id and hc_id_nh_key:
            new_fields[hc_id_nh_key] = str(customer_id)
        if merged_email and email_nh_key:
            new_fields[email_nh_key] = [merged_email]
        if merged_phone and phone_nh_key:
            new_fields[phone_nh_key] = [merged_phone]
        if merged_telegram and telegram_nh_key:
            new_fields[telegram_nh_key] = merged_telegram
        elif telegram_id and telegram_nh_key:
            new_fields[telegram_nh_key] = telegram_id
        if merged_instagram and instagram_nh_key:
            new_fields[instagram_nh_key] = merged_instagram

        new_fields.update(tracking_fields)

        if update_nh_link and chat_url:
            new_fields[nh_link_field] = chat_url
            details_log.append(f"Chat link '{chat_url}' will be written to field '{nh_link_field}' during contact creation.")

        created_contact, create_error = await nethunt.create_contact(nh_email, nh_key, nh_base, contacts_folder, new_fields)
        if created_contact:
            contact = created_contact
            is_new_contact = True
            search_method_used = "Auto-Created Card"
            details_log.append(f"Successfully created NetHunt Contact record ID: {contact.get('id')}")
            if tracking_fields:
                details_log.append(f"Wrote UTM & Referrer variables: {list(tracking_fields.keys())}")
        else:
            details_log.append("Failed to create new NetHunt contact card. Aborting.")
            if create_error:
                details_log.append(f"API error: {create_error}")
            add_log(event_type, cust_name, merged_email, merged_phone, "error", "\n".join(details_log), level="error", hc_customer_id=customer_id)
            return
    else:
        await _update_existing_contact(
            contact, customer_id, merged_email, merged_phone, merged_telegram,
            merged_instagram, tracking_fields, settings,
            hc_id_nh_key, email_nh_key, phone_nh_key, telegram_nh_key, instagram_nh_key,
            nh_email, nh_key, nh_base, details_log, telegram_id=telegram_id
        )

    contact_id = contact.get("id")
    contact_fields = contact.get("fields", {})
    contact_name = contact.get("name") or sync_engine._first_value(contact_fields.get(name_nh_key)) or sync_engine._first_value(contact_fields.get("Name")) or cust_name
    details_log.append(f"Using NetHunt Contact: Name='{contact_name}', ID={contact_id} ({search_method_used})")
    nh_field_keys = list(contact_fields.keys()) if contact_fields else []
    details_log.append(f"NetHunt contact fields available: {nh_field_keys}")

    # --- STEP 4: Write chat link ---
    if update_nh_link and chat_url and not is_new_contact:
        existing_link_raw = contact_fields.get(nh_link_field)
        existing_link = sync_engine._first_value(existing_link_raw).strip() if existing_link_raw else ""
        if existing_link != chat_url:
            details_log.append(f"Writing Chat Link '{chat_url}' to NetHunt field '{nh_link_field}' (priority write)...")
            nh_updated = await nethunt.update_contact_chat_link(nh_email, nh_key, nh_base, contact_id, nh_link_field, chat_url)
            if nh_updated:
                details_log.append("NetHunt CRM Contact updated with the HelpCrunch chat link.")
            else:
                details_log.append(f"Warning: Failed to update contact card field '{nh_link_field}' (ensure field exists in NetHunt Contacts folder).")
        else:
            details_log.append(f"Chat link already up-to-date in NetHunt field '{nh_link_field}'.")

    # --- STEP 5: Update local mirror ---
    try:
        await sync_engine.update_mirror_from_webhook(customer_data, chat_id, contact_id, contact)
        details_log.append("Local mirror updated with chat link and customer data.")
    except Exception:
        logger.exception("Failed to update local mirror from webhook:")

    # --- Build contact URLs ---
    contact_url = build_nethunt_record_url(nh_base, nh_workspace_id, contacts_folder, contact_id)
    short_contact_url = f"{nh_base}/app/records/{contacts_folder}/{contact_id}"
    details_log.append(f"NetHunt Contact Card URL: {contact_url}")

    # --- STEP 6: Bilateral sync to HelpCrunch ---
    hc_update_payload = {}
    hc_needs_name = (not cust_name or cust_name == "Unknown Customer" or cust_name.strip() == "")
    effective_name = contact_name if (contact_name and contact_name != "Unknown Customer") else (extracted_name or messenger_name or cust_name)
    if hc_needs_name:
        if contact_name and contact_name != "Unknown Customer":
            hc_update_payload["name"] = contact_name
            details_log.append(f"Pushing NetHunt name '{contact_name}' to HelpCrunch customer profile.")
        elif extracted_name:
            hc_update_payload["name"] = extracted_name
            details_log.append(f"Pushing extracted name '{extracted_name}' to HelpCrunch customer profile.")
        elif messenger_name:
            hc_update_payload["name"] = messenger_name
            details_log.append(f"Pushing messenger name '{messenger_name}' to HelpCrunch customer profile.")

    nh_email_val = sync_engine._first_value(contact_fields.get(email_nh_key))
    if not nh_email_val:
        for alt_key in ("Email", "email", "Електронна пошта", "E-mail", "Email Address"):
            if alt_key in contact_fields:
                nh_email_val = sync_engine._first_value(contact_fields[alt_key])
                if nh_email_val:
                    details_log.append(f"Found email in NetHunt field '{alt_key}' (configured key '{email_nh_key}' didn't match)")
                    break
    if nh_email_val and not cust_email and not merged_email:
        merged_email = nh_email_val
        details_log.append(f"Using email from NetHunt CRM: '{merged_email}'")
    if merged_email and (not cust_email or extracted_email):
        hc_update_payload["email"] = merged_email

    nh_phone_val = sync_engine._first_value(contact_fields.get(phone_nh_key))
    if not nh_phone_val:
        for alt_key in ("Phone", "phone", "Телефон", "Phone Number", "PhoneNumber", "Мобільний", "Mobile", "Tel"):
            if alt_key in contact_fields:
                nh_phone_val = sync_engine._first_value(contact_fields[alt_key])
                if nh_phone_val:
                    details_log.append(f"Found phone in NetHunt field '{alt_key}' (configured key '{phone_nh_key}' didn't match)")
                    break
    if nh_phone_val and not cust_phone and not merged_phone:
        merged_phone = nh_phone_val
        details_log.append(f"Using phone from NetHunt CRM: '{merged_phone}'")
    if merged_phone and (not cust_phone or extracted_phone):
        hc_update_payload["phone"] = merged_phone

    custom_data_updates = []
    if merged_telegram and not telegram_handle:
        custom_data_updates.append({"property": telegram_hc_key, "value": merged_telegram})
    if merged_instagram and not instagram_handle:
        custom_data_updates.append({"property": "instagram", "value": merged_instagram})
    custom_data_updates.append({"property": "nethunt_contact_url1", "value": contact_url})
    details_log.append(f"NetHunt contact URL to write: {contact_url} ({len(contact_url)} chars)")

    if hc_update_payload:
        details_log.append(f"Bilateral sync: updating HelpCrunch customer profile {customer_id} with {list(hc_update_payload.keys())}...")
        hc_updated, hc_error = await helpcrunch.update_customer(hc_api_key, customer_id, hc_update_payload)
        if hc_updated:
            details_log.append("HelpCrunch customer profile updated successfully.")
        else:
            details_log.append(f"Warning: HelpCrunch customer profile update failed. {hc_error}")

    if custom_data_updates:
        # Fetch fresh customer profile to get current customData (webhook payload may be incomplete)
        fresh_profile = None
        if hc_api_key and customer_id:
            try:
                fresh_profile = await helpcrunch.get_customer(hc_api_key, customer_id)
            except Exception:
                logger.exception(f"Failed to fetch fresh HC profile for customData merge: {customer_id}")
        existing_custom_data = (fresh_profile or customer_data).get("customData") or []
        if isinstance(existing_custom_data, list):
            merged_cd = [dict(item) if isinstance(item, dict) else item for item in existing_custom_data]
            existing_props = {item.get("property") for item in merged_cd if isinstance(item, dict)}
            for update in custom_data_updates:
                if update["property"] not in existing_props:
                    merged_cd.append(update)
                else:
                    for item in merged_cd:
                        if isinstance(item, dict) and item.get("property") == update["property"]:
                            item["value"] = update["value"]
                            break
            cd_payload = {"customData": merged_cd}
        elif isinstance(existing_custom_data, dict):
            merged_cd = [{"property": k, "value": v} for k, v in existing_custom_data.items()]
            existing_props = set(existing_custom_data.keys())
            for update in custom_data_updates:
                if update["property"] not in existing_props:
                    merged_cd.append(update)
                else:
                    for item in merged_cd:
                        if item["property"] == update["property"]:
                            item["value"] = update["value"]
                            break
            cd_payload = {"customData": merged_cd}
        else:
            cd_payload = {"customData": custom_data_updates}

        cd_props = [item.get("property") for item in cd_payload["customData"] if isinstance(item, dict)]
        details_log.append(f"Updating HelpCrunch customData: {cd_props}...")
        cd_updated, cd_error = await helpcrunch.update_customer(hc_api_key, customer_id, cd_payload)
        if cd_updated:
            details_log.append("HelpCrunch customData updated successfully.")
        else:
            details_log.append(f"Warning: HelpCrunch customData update failed. {cd_error}")
            # Fallback: save NetHunt URL to notes if customData failed
            if contact_url:
                notes_text = f"NetHunt CRM: {contact_url}"
                notes_ok, notes_err = await helpcrunch.update_customer_notes(hc_api_key, customer_id, notes_text)
                if notes_ok:
                    details_log.append("Fallback: saved NetHunt URL to customer notes.")
                else:
                    details_log.append(f"Fallback notes update also failed: {notes_err}")

    # --- STEP 7: Fetch deals ---
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

                d_stage = "N/A"
                for field_name in ["Stage", "Deal Stage", "Status", "Pipeline Stage", "pipelineStage"]:
                    if field_name in deal_fields:
                        d_stage = sync_engine._first_value(deal_fields[field_name])
                        break

                d_amount = ""
                for field_name in ["Amount", "Deal Amount", "Value", "value", "Price"]:
                    if field_name in deal_fields:
                        d_amount = f" - {sync_engine._first_value(deal_fields[field_name])}"
                        break

                d_link = build_nethunt_record_url(nh_base, nh_workspace_id, deals_folder, d_id)
                deals.append(f"- {d_name}: Stage={d_stage}{d_amount} (Link: {d_link})")

            deals_text = "\n".join(deals)
            details_log.append(f"Found {len(deals_raw)} related deals.")
        else:
            details_log.append("No active deals found.")
    elif is_new_contact:
        deals_text = "- No deals found (newly created contact card) -"

    # --- STEP 8: Write notes & private note ---
    card_prefix = "🟢 NEW" if is_new_contact else "🔴"
    formatted_notes = f"{card_prefix} NetHunt: {contact_name} (ID: {contact_id})"
    if len(formatted_notes) > 255:
        formatted_notes = formatted_notes[:252] + "..."

    details_log.append("Updating HelpCrunch customer notes...")
    notes_updated, notes_error = await helpcrunch.update_customer_notes(hc_api_key, customer_id, formatted_notes)
    if notes_updated:
        details_log.append("Customer notes updated successfully in HelpCrunch.")
    else:
        details_log.append(f"Warning: HelpCrunch customer notes update failed. {notes_error}")

    if chat_id:
        chat_note_md = (
            f"🔗 **NetHunt Integration Hub**\n\n"
            f"Created New Contact: [{contact_name}]({contact_url})\n"
            f"Active Deals:\n{deals_text}"
        ) if is_new_contact else (
            f"🔗 **NetHunt Integration Hub**\n\n"
            f"Matched Contact: [{contact_name}]({contact_url})\n"
            f"Active Deals:\n{deals_text if deals else '- No deals found -'}"
        )
        chat_note_plain = (
            f"NetHunt Integration Hub\n\n"
            f"{'Created New Contact' if is_new_contact else 'Matched Contact'}: {contact_name}\n"
            f"URL: {contact_url}\n"
            f"Active Deals: {deals_text if deals else '- No deals found -'}"
        )
        details_log.append(f"Adding private note to chat ID {chat_id}...")
        private_note_added, note_error = await helpcrunch.add_private_note(hc_api_key, chat_id, chat_note_plain, chat_note_md)
        if private_note_added:
            details_log.append("Private note added to the chat inbox.")
        else:
            details_log.append(f"Warning: Could not add private note to the chat inbox. {note_error}")

    log_level = "warning" if any("Warning:" in d for d in details_log) else "info"
    add_log(event_type, contact_name, merged_email, merged_phone, "success", "\n".join(details_log), level=log_level, hc_customer_id=customer_id)
    logger.info(f"Sync task completed successfully for customer {cust_name}")


async def process_sync_task(
    event_type: str,
    customer_data: dict,
    chat_id: Optional[int] = None,
    message_text: Optional[str] = None
):
    """Wrapper around _process_sync_task with per-customer locking and exception handling."""
    customer_name = customer_data.get("name") or "Unknown Customer"
    customer_email = customer_data.get("email") or ""
    customer_phone = customer_data.get("phone") or ""
    customer_id = customer_data.get("id")
    try:
        if customer_id:
            lock = await _get_customer_lock(customer_id)
            async with lock:
                await _process_sync_task(event_type, customer_data, chat_id, message_text)
        else:
            await _process_sync_task(event_type, customer_data, chat_id, message_text)
    except Exception as e:
        logger.exception("Unhandled error during sync task:")
        error_details = f"Unhandled exception: {e}\n{traceback.format_exc()}"
        add_log(event_type, customer_name, customer_email, customer_phone, "error", error_details, level="error", hc_customer_id=customer_id)
        logger.error(f"Sync task failed for customer {customer_name}: {e}")
