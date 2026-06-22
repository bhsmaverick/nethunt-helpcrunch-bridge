import json
import logging
from datetime import datetime
from typing import Optional

from .database import (
    get_settings,
    save_hc_customer,
    save_hc_chat,
    save_nh_contact,
    save_nh_deal,
    save_match_link,
    get_nh_contact_by_id,
    find_nh_contact_by_chat_link,
    find_nh_contact_by_phone,
    find_nh_contact_by_email,
    find_nh_contact_by_telegram,
    find_nh_contact_by_instagram,
    find_match_by_hc_customer_id,
    get_db_connection,
    clear_mirror_data,
)
from .services import helpcrunch, nethunt
from .extractors import (
    normalize_email,
    normalize_phone,
    normalize_telegram,
    normalize_instagram,
    build_chat_link,
)

logger = logging.getLogger("bridge")


def _now() -> str:
    return datetime.now().isoformat()


def _first_value(val):
    """Extracts a single string value from a possibly multi-value NetHunt field."""
    if not val:
        return ""
    if isinstance(val, list):
        for v in val:
            if v is not None and str(v).strip():
                return str(v)
        return ""
    return str(val)


def parse_hc_customer_profile(customer_data: dict, settings: dict) -> dict:
    """Extracts normalized contact values from a HelpCrunch customer payload."""
    if not customer_data:
        return {}

    telegram_key = settings.get("telegram_field_hc", "telegram")
    telegram = ""
    instagram = ""

    custom_data = customer_data.get("customData")
    if custom_data:
        if isinstance(custom_data, list):
            for item in custom_data:
                if isinstance(item, dict):
                    prop = item.get("property") or item.get("name")
                    val = item.get("value") or ""
                    if prop == telegram_key:
                        telegram = val
                    elif prop == "instagram":
                        instagram = val
        elif isinstance(custom_data, dict):
            telegram = custom_data.get(telegram_key) or ""
            instagram = custom_data.get("instagram") or ""

    return {
        "hc_customer_id": customer_data.get("id"),
        "name": customer_data.get("name") or "",
        "email": normalize_email(customer_data.get("email") or ""),
        "phone": normalize_phone(customer_data.get("phone") or ""),
        "telegram": normalize_telegram(telegram),
        "instagram": normalize_instagram(instagram),
    }


def parse_nh_contact(record: dict, folder_id: str, settings: dict) -> dict:
    """Extracts normalized contact values from a NetHunt record."""
    fields = record.get("fields", {}) or {}
    record_id = record.get("id") or record.get("recordId")

    email_field = settings.get("email_field_nh", "Email")
    phone_field = settings.get("phone_field_nh", "Phone")
    telegram_field = settings.get("telegram_field_nh", "Telegram")
    instagram_field = settings.get("instagram_field_nh", "Instagram")
    chat_link_field = settings.get("nh_chat_link_field", "HelpCrunch Chat Link")

    email = normalize_email(_first_value(fields.get(email_field)))
    phone = normalize_phone(_first_value(fields.get(phone_field)))
    telegram = normalize_telegram(_first_value(fields.get(telegram_field)))
    instagram = normalize_instagram(_first_value(fields.get(instagram_field)))
    chat_link = _first_value(fields.get(chat_link_field)).strip()

    name = record.get("name") or _first_value(fields.get("Name")) or ""

    return {
        "nh_record_id": str(record_id),
        "folder_id": folder_id,
        "name": name,
        "email": email,
        "phone": phone,
        "telegram": telegram,
        "instagram": instagram,
        "chat_link": chat_link,
    }


def parse_nh_deal(record: dict, folder_id: str) -> dict:
    """Extracts key fields from a NetHunt deal record."""
    fields = record.get("fields", {}) or {}
    record_id = record.get("id") or record.get("recordId")
    name = record.get("name") or _first_value(fields.get("Name")) or "Untitled Deal"

    stage = ""
    for field_name in ["Stage", "Deal Stage", "Status", "Pipeline Stage", "pipelineStage"]:
        if field_name in fields and fields[field_name] is not None:
            stage = _first_value(fields[field_name])
            break

    amount = ""
    for field_name in ["Amount", "Deal Amount", "Value", "value", "Price"]:
        if field_name in fields and fields[field_name] is not None:
            amount = _first_value(fields[field_name])
            break

    contact_id = ""
    for field_name in ["Contact", "Контакт", "Клієнт", "contact", "Customer"]:
        if field_name in fields and fields[field_name] is not None:
            contact_id = _first_value(fields[field_name])
            break

    return {
        "nh_record_id": str(record_id),
        "folder_id": folder_id,
        "contact_id": contact_id,
        "name": name,
        "stage": stage,
        "amount": amount,
    }


async def sync_hc_customers(api_key: str, max_pages: int = 1000) -> int:
    """Fetches all HelpCrunch customers and saves them to the local mirror."""
    settings = get_settings()
    customers = await helpcrunch.list_all_customers(api_key, max_pages=max_pages)
    count = 0
    for customer in customers:
        try:
            profile = parse_hc_customer_profile(customer, settings)
            if not profile.get("hc_customer_id"):
                continue
            save_hc_customer(
                hc_customer_id=profile["hc_customer_id"],
                name=profile["name"],
                email=profile["email"],
                phone=profile["phone"],
                telegram=profile["telegram"],
                instagram=profile["instagram"],
                raw_json=json.dumps(customer),
            )
            count += 1
        except Exception:
            logger.exception("Failed to mirror HelpCrunch customer")
    return count


async def sync_hc_chats(api_key: str, subdomain: str, max_pages: int = 1000) -> int:
    """Fetches all HelpCrunch chats and saves them to the local mirror."""
    chats = await helpcrunch.list_all_chats(api_key, max_pages=max_pages)
    count = 0
    for chat in chats:
        try:
            chat_id = chat.get("id")
            if not chat_id:
                continue
            customer = chat.get("customer") or {}
            customer_id = customer.get("id") if isinstance(customer, dict) else customer
            chat_link = build_chat_link(subdomain, chat_id) if subdomain else ""
            save_hc_chat(
                hc_chat_id=chat_id,
                hc_customer_id=customer_id,
                status=chat.get("status") or "",
                chat_link=chat_link,
                raw_json=json.dumps(chat),
                created_at=chat.get("createdAt") or "",
                updated_at=chat.get("updatedAt") or "",
            )
            count += 1
        except Exception:
            logger.exception("Failed to mirror HelpCrunch chat")
    return count


async def sync_nh_contacts(email: str, api_key: str, base_url: str, folder_id: str) -> int:
    """Fetches all NetHunt contacts from a folder and saves them to the local mirror."""
    settings = get_settings()
    records = await nethunt.find_all_records(email, api_key, base_url, folder_id)
    count = 0
    for record in records:
        try:
            parsed = parse_nh_contact(record, folder_id, settings)
            save_nh_contact(
                nh_record_id=parsed["nh_record_id"],
                folder_id=parsed["folder_id"],
                name=parsed["name"],
                email=parsed["email"],
                phone=parsed["phone"],
                telegram=parsed["telegram"],
                instagram=parsed["instagram"],
                chat_link=parsed["chat_link"],
                hc_customer_id=None,
                raw_json=json.dumps(record),
            )
            count += 1
        except Exception:
            logger.exception("Failed to mirror NetHunt contact")
    return count


async def sync_nh_deals(email: str, api_key: str, base_url: str, folder_id: str) -> int:
    """Fetches all NetHunt deals from a folder and saves them to the local mirror."""
    records = await nethunt.find_all_records(email, api_key, base_url, folder_id)
    count = 0
    for record in records:
        try:
            parsed = parse_nh_deal(record, folder_id)
            save_nh_deal(
                nh_record_id=parsed["nh_record_id"],
                folder_id=parsed["folder_id"],
                contact_id=parsed["contact_id"],
                name=parsed["name"],
                stage=parsed["stage"],
                amount=parsed["amount"],
                raw_json=json.dumps(record),
            )
            count += 1
        except Exception:
            logger.exception("Failed to mirror NetHunt deal")
    return count


async def run_matching():
    """Creates match links between HC customers and NH contacts in the local mirror."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. High-confidence match by chat_link
        cursor.execute("SELECT hc_chat_id, hc_customer_id, chat_link FROM hc_chats WHERE chat_link != ''")
        for chat in cursor.fetchall():
            if not chat["hc_customer_id"]:
                continue
            contact = find_nh_contact_by_chat_link(chat["chat_link"])
            if contact:
                save_match_link(chat["hc_customer_id"], contact["nh_record_id"], "chat_link", "high")
                cursor.execute(
                    "UPDATE nh_contacts SET hc_customer_id = ? WHERE nh_record_id = ?",
                    (chat["hc_customer_id"], contact["nh_record_id"]),
                )

        # 2. Match remaining unmatched HC customers by contact fields
        cursor.execute(
            "SELECT hc_customer_id, email, phone, telegram, instagram FROM hc_customers"
        )
        for customer in cursor.fetchall():
            hc_customer_id = customer["hc_customer_id"]
            existing = find_match_by_hc_customer_id(hc_customer_id)
            if existing:
                continue

            contact = None
            method = None
            if customer["phone"]:
                contact = find_nh_contact_by_phone(customer["phone"])
                method = "phone"
            if not contact and customer["email"]:
                contact = find_nh_contact_by_email(customer["email"])
                method = "email"
            if not contact and customer["telegram"]:
                contact = find_nh_contact_by_telegram(customer["telegram"])
                method = "telegram"
            if not contact and customer["instagram"]:
                contact = find_nh_contact_by_instagram(customer["instagram"])
                method = "instagram"

            if contact and method:
                confidence = "medium" if method in ("telegram", "instagram") else "high"
                save_match_link(hc_customer_id, contact["nh_record_id"], method, confidence)
                cursor.execute(
                    "UPDATE nh_contacts SET hc_customer_id = ? WHERE nh_record_id = ?",
                    (hc_customer_id, contact["nh_record_id"]),
                )

        conn.commit()
    except Exception:
        logger.exception("Error during local mirror matching")
        conn.rollback()
    finally:
        conn.close()


async def run_full_sync(clear_existing: bool = False) -> dict:
    """Runs a full historical sync of CRM and HelpCrunch data into the local mirror."""
    try:
        settings = get_settings()
        api_key = settings.get("helpcrunch_api_key")
        subdomain = settings.get("helpcrunch_subdomain", "")
        nh_email = settings.get("nethunt_api_email")
        nh_key = settings.get("nethunt_api_key")
        nh_base = settings.get("nethunt_base_url", "https://nethunt.co")
        contacts_folder = settings.get("nethunt_contacts_folder")
        deals_folder = settings.get("nethunt_deals_folder")

        if not all([api_key, nh_email, nh_key, contacts_folder]):
            raise ValueError("Missing credentials or NetHunt contacts folder for full sync")

        if clear_existing:
            clear_mirror_data()

        hc_customer_count = await sync_hc_customers(api_key)
        hc_chat_count = await sync_hc_chats(api_key, subdomain)
        nh_contact_count = await sync_nh_contacts(nh_email, nh_key, nh_base, contacts_folder)
        nh_deal_count = 0
        if deals_folder:
            nh_deal_count = await sync_nh_deals(nh_email, nh_key, nh_base, deals_folder)

        await run_matching()

        logger.info(
            f"Full sync completed: {hc_customer_count} HC customers, {hc_chat_count} HC chats, "
            f"{nh_contact_count} NH contacts, {nh_deal_count} NH deals"
        )
        return {
            "hc_customers": hc_customer_count,
            "hc_chats": hc_chat_count,
            "nh_contacts": nh_contact_count,
            "nh_deals": nh_deal_count,
        }
    except Exception as e:
        logger.exception("Full sync failed:")
        return {"error": str(e)}


async def resolve_nh_contact(customer_data: dict, chat_id: Optional[int]) -> Optional[dict]:
    """Finds the best matching NetHunt contact in the local mirror for an incoming chat."""
    settings = get_settings()
    subdomain = settings.get("helpcrunch_subdomain", "")

    # 1. Match by chat_link (highest priority)
    if chat_id and subdomain:
        chat_link = build_chat_link(subdomain, chat_id)
        contact = find_nh_contact_by_chat_link(chat_link)
        if contact:
            return contact

    # 2. Extract normalized profile values
    profile = parse_hc_customer_profile(customer_data, settings)
    hc_customer_id = profile.get("hc_customer_id")

    # 3. Existing match link for this HC customer
    if hc_customer_id:
        match = find_match_by_hc_customer_id(hc_customer_id)
        if match:
            contact = get_nh_contact_by_id(match["nh_contact_id"])
            if contact:
                return contact

    # 4. Field-based match
    if profile.get("phone"):
        contact = find_nh_contact_by_phone(profile["phone"])
        if contact:
            return contact
    if profile.get("email"):
        contact = find_nh_contact_by_email(profile["email"])
        if contact:
            return contact
    if profile.get("telegram"):
        contact = find_nh_contact_by_telegram(profile["telegram"])
        if contact:
            return contact
    if profile.get("instagram"):
        contact = find_nh_contact_by_instagram(profile["instagram"])
        if contact:
            return contact

    return None


async def update_mirror_from_webhook(customer_data: dict, chat_id: Optional[int], contact_id: Optional[str], nh_contact_data: Optional[dict] = None):
    """Updates the local mirror after a chat webhook has been processed."""
    settings = get_settings()
    profile = parse_hc_customer_profile(customer_data, settings)
    hc_customer_id = profile.get("hc_customer_id")
    if not hc_customer_id:
        return

    save_hc_customer(
        hc_customer_id=hc_customer_id,
        name=profile["name"],
        email=profile["email"],
        phone=profile["phone"],
        telegram=profile["telegram"],
        instagram=profile["instagram"],
        raw_json=json.dumps(customer_data),
    )

    if chat_id:
        subdomain = settings.get("helpcrunch_subdomain", "")
        chat_link = build_chat_link(subdomain, chat_id) if subdomain else ""
        save_hc_chat(
            hc_chat_id=chat_id,
            hc_customer_id=hc_customer_id,
            status="",
            chat_link=chat_link,
            raw_json=json.dumps({"customer_id": hc_customer_id}),
            created_at=_now(),
            updated_at=_now(),
        )
        # If we know the CRM contact, link by chat_link as well
        if contact_id:
            contact = get_nh_contact_by_id(contact_id)
            if contact:
                save_nh_contact(
                    nh_record_id=contact_id,
                    folder_id=contact["folder_id"],
                    name=contact["name"],
                    email=contact["email"],
                    phone=contact["phone"],
                    telegram=contact["telegram"],
                    instagram=contact["instagram"],
                    chat_link=chat_link,
                    hc_customer_id=hc_customer_id,
                    raw_json=contact["raw_json"],
                )
                save_match_link(hc_customer_id, contact_id, "chat_link", "high")
            elif nh_contact_data:
                # Newly created contact not yet in local mirror — save it
                contacts_folder = settings.get("nethunt_contacts_folder", "")
                parsed = parse_nh_contact(nh_contact_data, contacts_folder, settings)
                save_nh_contact(
                    nh_record_id=parsed["nh_record_id"],
                    folder_id=parsed["folder_id"],
                    name=parsed["name"],
                    email=parsed["email"],
                    phone=parsed["phone"],
                    telegram=parsed["telegram"],
                    instagram=parsed["instagram"],
                    chat_link=chat_link,
                    hc_customer_id=hc_customer_id,
                    raw_json=json.dumps(nh_contact_data),
                )
                save_match_link(hc_customer_id, parsed["nh_record_id"], "chat_link", "high")
    elif contact_id:
        # No chat_id, but we still have a CRM contact match
        contact = get_nh_contact_by_id(contact_id)
        if contact and not contact.get("hc_customer_id"):
            save_nh_contact(
                nh_record_id=contact_id,
                folder_id=contact["folder_id"],
                name=contact["name"],
                email=contact["email"],
                phone=contact["phone"],
                telegram=contact["telegram"],
                instagram=contact["instagram"],
                chat_link=contact["chat_link"],
                hc_customer_id=hc_customer_id,
                raw_json=contact["raw_json"],
            )
            save_match_link(hc_customer_id, contact_id, "webhook", "high")
        elif not contact and nh_contact_data:
            # Newly created contact not yet in local mirror — save it
            contacts_folder = settings.get("nethunt_contacts_folder", "")
            parsed = parse_nh_contact(nh_contact_data, contacts_folder, settings)
            save_nh_contact(
                nh_record_id=parsed["nh_record_id"],
                folder_id=parsed["folder_id"],
                name=parsed["name"],
                email=parsed["email"],
                phone=parsed["phone"],
                telegram=parsed["telegram"],
                instagram=parsed["instagram"],
                chat_link=parsed["chat_link"],
                hc_customer_id=hc_customer_id,
                raw_json=json.dumps(nh_contact_data),
            )
            save_match_link(hc_customer_id, parsed["nh_record_id"], "webhook", "high")
