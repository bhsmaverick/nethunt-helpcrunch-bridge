import re
import json
import base64
from typing import Optional
from urllib.parse import urlparse, parse_qs, quote

try:
    from .names_uk import NAMES_SET, is_likely_name
except ImportError:
    NAMES_SET = frozenset()
    def is_likely_name(word: str) -> bool:
        return bool(word) and len(word) >= 2

# Cyrillic character ranges (Ukrainian + Russian + Latin)
_CYR_LAT = r'а-щьюяієїґА-ЩЬЮЯІЄЇҐa-zA-Z'

# Common false positives for name extraction
_NAME_STOPWORDS = frozenset({
    'не', 'тут', 'here', 'there', 'bot', 'бот', 'привет', 'hi', 'hello',
    'добрий', 'доброго', 'здравствуйте', 'так', 'ні', 'yes', 'no',
    'дякую', 'спасибо', 'ok', 'ок', 'добре', 'вечір',
    'день', 'ранок', 'було', 'немає', 'можна', 'будь', 'ласка',
    'питання', 'консультація', 'адвокат', 'юрист', 'закон',
})

# Name extraction patterns (tried in order)
_NAME_PATTERNS = [
    r'(?:мене\s+звати|я\s+є|мо[єю]\s+ім[\'\u2019]?я)\s*[:,]?\s*([а-щьюяієїґА-ЩЬЮЯІЄЇҐa-zA-Z]{2,40})',
    r'^це\s+([а-щьюяієїґА-ЩЬЮЯІЄЇҐa-zA-Z]{2,40})',
    r'(?:меня\s+зовут|мо[ёю]\s+имя)\s*[:,]?\s*([а-яёА-ЯЁa-zA-Z]{2,40})',
    r'(?:my\s+name\s+is|i\s+am|i\'m|this\s+is)\s+([a-zA-Z]{2,40})',
    r'(?:ім[\'\u2019]?я|name|імя|имя)\s*[:=]\s*([а-щьюяієїґА-ЩЬЮЯІЄЇҐa-zA-Z]{2,40})',
    r'(?:можна|звати|просто|я\s+же|це\s+ж)\s+([а-щьюяієїґА-ЩЬЮЯІЄЇҐa-zA-Z]{2,40})',
    r'(?:звертайтеся|називайте|обращайтесь|называйте)\s+([а-щьюяієїґА-ЩЬЮЯІЄЇҐа-яёА-ЯЁa-zA-Z]{2,40})',
]


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


def _is_valid_name(name: str) -> bool:
    """Check if a candidate name is not a stopword."""
    return name.lower() not in _NAME_STOPWORDS


def _extract_name_near_phone(text: str, phone: str) -> Optional[str]:
    """Find a name adjacent to a phone number in text."""
    phone_digits = re.sub(r'[^\d]', '', phone)
    if len(phone_digits) < 6:
        return None
    for match_len in (9, 8, 7, 6):
        phone_part = phone_digits[-match_len:]
        phone_pos = text.find(phone_part)
        if phone_pos == -1:
            continue
        phone_end = phone_pos + len(phone_part)
        # Name BEFORE phone
        before_text = re.sub(r'[^а-щьюяієїґА-ЩЬЮЯІЄЇҐa-zA-Z]+$', '', text[:phone_pos].rstrip())
        m = re.search(rf'([{_CYR_LAT}]{{2,40}})\s*[,\.]?$', before_text)
        if m and _is_valid_name(m.group(1)):
            return m.group(1).strip().capitalize()
        # Name AFTER phone
        after_text = text[phone_end:].lstrip()
        m = re.match(rf'[,\.]?\s*([{_CYR_LAT}]{{2,40}})', after_text)
        if m and _is_valid_name(m.group(1)):
            return m.group(1).strip().capitalize()
        break
    return None


def _extract_standalone_name(text_stripped: str) -> Optional[str]:
    """Extract a standalone name from a short message (1-3 words, all Cyrillic)."""
    words = text_stripped.split()
    if not (1 <= len(words) <= 3):
        return None
    name_word_re = re.compile(r"^[А-ЩЬЮЯІЄЇҐа-щьюяієїґ]+(?:[-\u2019']?[А-ЩЬЮЯІЄЇҐа-щьюяієїґ]+)?$")
    if not all(name_word_re.match(w) for w in words):
        return None
    if not _is_valid_name(words[0]) or not words[0][0].isupper():
        return None
    if is_likely_name(words[0].capitalize()):
        return ' '.join(w.capitalize() for w in words)
    return None


def extract_name(text: str, phone: str = None) -> Optional[str]:
    """Extracts a person's name from chat message text.
    Supports Ukrainian, Russian, and English patterns.
    Also tries to find a name near a phone number if provided.
    """
    if not text:
        return None
    text_stripped = text.strip()

    # Try regex patterns first
    for pattern in _NAME_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match and _is_valid_name(match.group(1)):
            return match.group(1).strip().capitalize()

    # Try name near phone
    if phone:
        name = _extract_name_near_phone(text, phone)
        if name:
            return name

    # Try standalone name
    return _extract_standalone_name(text_stripped)


def build_chat_link(subdomain: str, chat_id: int) -> str:
    """Builds a HelpCrunch chat URL from subdomain and chat id."""
    return f"https://{subdomain.strip().rstrip('.')}.helpcrunch.com/inbox/chats/{chat_id}"


def build_nethunt_record_url(base_url: str, workspace_id: str, folder_id: str, record_id: str) -> str:
    """Builds a NetHunt CRM record URL in the correct web app format.

    Format: {base_url}/web/#nethunt/{base64(json)}
    Where json = {"workspaceId":"...","folderId":"...","recordId":"...","recordPage":{"recordId":"..."}}
    Uses base64 of raw JSON (no URL encoding) — NetHunt opens this correctly.
    """
    if not workspace_id or not folder_id or not record_id:
        return f"{base_url.rstrip('/')}/web/"
    payload = {
        "workspaceId": workspace_id,
        "folderId": folder_id,
        "recordId": record_id,
        "recordPage": {"recordId": record_id}
    }
    json_str = json.dumps(payload, separators=(",", ":"))
    b64_encoded = base64.b64encode(json_str.encode()).decode()
    return f"{base_url.rstrip('/')}/web/#nethunt/{b64_encoded}"
