import re
from typing import Optional
from urllib.parse import urlparse, parse_qs


def extract_email(text: str) -> Optional[str]:
    if not text:
        return None
    email_pattern = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
    match = email_pattern.search(text)
    return match.group(0) if match else None


def extract_phone(text: str) -> Optional[str]:
    if not text:
        return None
    phone_pattern = re.compile(r'\+?\d[\d\-\(\)\s]{7,14}\d')
    matches = phone_pattern.findall(text)
    for match in matches:
        cleaned = re.sub(r'[^\d+]', '', match)
        digit_count = sum(c.isdigit() for c in cleaned)
        if 9 <= digit_count <= 15:
            if cleaned.startswith("+"):
                return cleaned
            if cleaned.startswith("380") and len(cleaned) == 12:
                return "+" + cleaned
            if cleaned.startswith("0") and len(cleaned) == 10:
                return "+38" + cleaned
            if len(cleaned) == 9 and cleaned.startswith(("50", "63", "66", "67", "68", "73", "89", "91", "92", "93", "94", "95", "96", "97", "98", "99")):
                return "+380" + cleaned
            if len(cleaned) >= 10:
                return "+" + cleaned
            return cleaned
    return None


def extract_messengers(text: str) -> dict:
    results = {}
    if not text:
        return results

    text_lower = text.lower()

    # 1. Telegram link (t.me/handle)
    tg_link_match = re.search(r'(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})', text)
    if tg_link_match:
        results["telegram"] = tg_link_match.group(1)
    else:
        # 2. Telegram prefix (telegram: @handle, tg: @handle)
        tg_prefix_match = re.search(r'\b(?:tg|telegram|телеграм|тг)(?:\s*[:=-]\s*@?|\s+@)([a-zA-Z0-9_]{5,32})', text_lower)
        if tg_prefix_match:
            start, end = tg_prefix_match.span(1)
            results["telegram"] = text[start:end]

    # 3. Instagram link (instagram.com/handle)
    ig_link_match = re.search(r'(?:instagram\.com|instagr\.am)/([a-zA-Z0-9_.]+)', text)
    if ig_link_match:
        handle = ig_link_match.group(1)
        if handle.endswith("/"):
            handle = handle[:-1]
        results["instagram"] = handle
    else:
        # 4. Instagram prefix (instagram: @handle, insta: @handle)
        ig_prefix_match = re.search(r'\b(?:instagram|insta|інстаграм|інста|ig)(?:\s*[:=-]\s*@?|\s+@)([a-zA-Z0-9_.]+)', text_lower)
        if ig_prefix_match:
            start, end = ig_prefix_match.span(1)
            results["instagram"] = text[start:end]

    # 5. Bare @handle — only if no explicit Telegram or Instagram match was found
    if "telegram" not in results:
        at_matches = re.finditer(r'@([a-zA-Z0-9_]{5,32})', text)
        for m in at_matches:
            start_idx = m.start()
            if start_idx == 0 or not text[start_idx-1].isalnum():
                # Check if this @ is part of an Instagram context
                prefix_text = text[:start_idx].lower()
                if re.search(r'\b(?:instagram|insta|інстаграм|інста|ig)\s*[:=\-]?\s*$', prefix_text):
                    continue
                domain_text = text[m.end():m.end()+10]
                if not re.match(r'^\.[a-zA-Z]{2,4}', domain_text):
                    results["telegram"] = m.group(1)
                    break

    return results


def extract_params_from_url(url_str: str) -> dict:
    if not url_str:
        return {}
    try:
        parsed = urlparse(url_str)
        return {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
    except Exception:
        return {}


def detect_platform_from_url(url_str: str) -> str:
    if not url_str:
        return ""
    url_lower = url_str.lower()
    if "t.me" in url_lower or "telegram.org" in url_lower:
        return "Telegram"
    if "instagram.com" in url_lower or "instagr.am" in url_lower:
        return "Instagram"
    if "facebook.com" in url_lower or "fb.com" in url_lower:
        return "Facebook"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "YouTube"
    if "viber.com" in url_lower:
        return "Viber"
    if "whatsapp.com" in url_lower or "wa.me" in url_lower:
        return "WhatsApp"
    if "google" in url_lower:
        return "Google"
    return ""


def normalize_email(value: str) -> str:
    if not value:
        return ""
    extracted = extract_email(value)
    if extracted:
        return extracted.lower().strip()
    return str(value).lower().strip()


def normalize_phone(value: str) -> str:
    if not value:
        return ""
    # extract_phone also normalizes country prefixes
    return extract_phone(str(value)) or ""


def normalize_telegram(value: str) -> str:
    if not value:
        return ""
    handle = str(value).strip().lstrip("@").lower()
    return handle


def normalize_instagram(value: str) -> str:
    if not value:
        return ""
    handle = str(value).strip().lstrip("@").rstrip("/").lower()
    return handle


def extract_chat_id_from_url(chat_url: str) -> Optional[int]:
    """Extracts the numeric chat id from a HelpCrunch chat URL."""
    if not chat_url:
        return None
    try:
        parsed = urlparse(chat_url)
        path = parsed.path or ""
        # Expected path: /inbox/chats/{chat_id}
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[-2] == "chats":
            return int(parts[-1])
    except Exception:
        pass
    return None


def build_chat_link(subdomain: str, chat_id: int) -> str:
    """Builds a HelpCrunch chat URL from subdomain and chat id."""
    return f"https://{subdomain.strip().rstrip('.')}.helpcrunch.com/inbox/chats/{chat_id}"
