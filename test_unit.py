"""
Unit tests for NetHunt-HelpCrunch bridge logic.
Tests cover: get_contact, find_contact, _normalize_records_response,
bilateral sync payload, save_match_link self-healing, local mirror validation.

Run: python -m pytest test_unit.py -v
"""

import asyncio
import json
import os
import re
import sys
from unittest.mock import AsyncMock, MagicMock, patch as mock_patch

import pytest

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


def _first_value(raw):
    """Inline copy of sync_engine._first_value to avoid relative import issues."""
    if not raw:
        return ""
    if isinstance(raw, list):
        return str(raw[0]).strip() if raw else ""
    return str(raw).strip()


# ─── Helpers ──────────────────────────────────────────────────────────────

NAZARENKO = {
    "id": "rec_nazarenko",
    "recordId": "rec_nazarenko",
    "name": "Назаренко Петро",
    "fields": {
        "HelpCrunch ID": ["99999"],
        "Phone": ["+380226767676"],
        "Email": ["nazarenko@example.com"],
        "Telegram": ["petro_tg"],
    },
}

SANIA = {
    "id": "rec_sania",
    "recordId": "rec_sania",
    "name": "Саня",
    "fields": {
        "HelpCrunch ID": ["88888"],
        "Phone": [],
        "Email": [],
        "Telegram": ["Clame24"],
    },
}

NEW_GUY = {
    "id": "rec_new",
    "recordId": "rec_new",
    "name": "New Lead",
    "fields": {
        "HelpCrunch ID": ["77777"],
        "Phone": ["+380501112233"],
        "Email": ["new@example.com"],
        "Telegram": ["newguy_tg"],
    },
}


# ─── 1. _normalize_records_response ───────────────────────────────────────

class TestNormalizeRecords:
    def test_list_of_dicts(self):
        from services.nethunt import _normalize_records_response
        data = [{"recordId": "r1", "fields": {}}, {"recordId": "r2", "fields": {}}]
        result = _normalize_records_response(data)
        assert len(result) == 2
        assert result[0]["id"] == "r1"
        assert result[1]["id"] == "r2"

    def test_bare_single_record_dict(self):
        from services.nethunt import _normalize_records_response
        data = {"recordId": "r1", "fields": {"Name": ["Test"]}}
        result = _normalize_records_response(data)
        assert len(result) == 1
        assert result[0]["id"] == "r1"

    def test_wrapped_in_data(self):
        from services.nethunt import _normalize_records_response
        data = {"data": [{"recordId": "r1", "fields": {}}]}
        result = _normalize_records_response(data)
        assert len(result) == 1
        assert result[0]["id"] == "r1"

    def test_wrapped_in_records(self):
        from services.nethunt import _normalize_records_response
        data = {"records": [{"recordId": "r1", "fields": {}}]}
        result = _normalize_records_response(data)
        assert len(result) == 1
        assert result[0]["id"] == "r1"

    def test_empty(self):
        from services.nethunt import _normalize_records_response
        assert _normalize_records_response([]) == []
        assert _normalize_records_response({}) == []
        assert _normalize_records_response(None) == []

    def test_already_has_id(self):
        from services.nethunt import _normalize_records_response
        data = [{"id": "r1", "fields": {}}]
        result = _normalize_records_response(data)
        assert result[0]["id"] == "r1"


# ─── 2. get_contact — strict ID match ─────────────────────────────────────

class TestGetContact:
    def test_exact_match_by_id(self):
        from services import nethunt
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [NAZARENKO, SANIA]
        with mock_patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = asyncio.run(
                nethunt.get_contact("e", "k", "https://nethunt.com", "rec_nazarenko", "folder1")
            )
        assert result is not None
        assert result["id"] == "rec_nazarenko"

    def test_exact_match_by_recordId_only(self):
        from services import nethunt
        record = {"recordId": "rec_xyz", "fields": {"Name": ["Test"]}}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [record]
        with mock_patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = asyncio.run(
                nethunt.get_contact("e", "k", "https://nethunt.com", "rec_xyz", "folder1")
            )
        assert result is not None
        assert result.get("id") == "rec_xyz"

    def test_no_match_returns_none(self):
        from services import nethunt
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [NAZARENKO, SANIA]
        with mock_patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = asyncio.run(
                nethunt.get_contact("e", "k", "https://nethunt.com", "rec_nonexistent", "folder1")
            )
        assert result is None

    def test_empty_record_id_returns_none(self):
        from services import nethunt
        result = asyncio.run(
            nethunt.get_contact("e", "k", "https://nethunt.com", "", "folder1")
        )
        assert result is None


# ─── 3. find_contact — validation ─────────────────────────────────────────

class TestFindContact:
    def test_validated_match_found(self):
        from services import nethunt
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [NAZARENKO, SANIA]
        with mock_patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = asyncio.run(
                nethunt.find_contact("e", "k", "https://nethunt.com", "folder1",
                                     '"Telegram":"Clame24"',
                                     expected_field="Telegram", expected_value="Clame24")
            )
        assert result is not None
        assert result["id"] == "rec_sania"

    def test_validated_no_match_returns_none(self):
        from services import nethunt
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [NAZARENKO, SANIA]
        with mock_patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = asyncio.run(
                nethunt.find_contact("e", "k", "https://nethunt.com", "folder1",
                                     '"Telegram":"nonexistent"',
                                     expected_field="Telegram", expected_value="nonexistent")
            )
        assert result is None

    def test_tolerant_phone_match(self):
        from services import nethunt
        record = {"id": "r1", "fields": {"Phone": ["+38 (050) 111-22-33"]}}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [record]
        with mock_patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = asyncio.run(
                nethunt.find_contact("e", "k", "https://nethunt.com", "folder1",
                                     '"Phone":"+380501112233"',
                                     expected_field="Phone", expected_value="+380501112233")
            )
        assert result is not None
        assert result["id"] == "r1"

    def test_tolerant_tg_match_with_at(self):
        from services import nethunt
        record = {"id": "r1", "fields": {"Telegram": ["@Clame24"]}}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [record]
        with mock_patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = asyncio.run(
                nethunt.find_contact("e", "k", "https://nethunt.com", "folder1",
                                     '"Telegram":"Clame24"',
                                     expected_field="Telegram", expected_value="Clame24")
            )
        assert result is not None
        assert result["id"] == "r1"

    def test_no_validation_returns_first(self):
        from services import nethunt
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [NAZARENKO, SANIA]
        with mock_patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = asyncio.run(
                nethunt.find_contact("e", "k", "https://nethunt.com", "folder1",
                                     '"HelpCrunch ID":"99999"')
            )
        assert result is not None
        assert result["id"] == "rec_nazarenko"


# ─── 4. Bilateral sync payload ────────────────────────────────────────────

class TestBilateralSyncPayload:
    """
    Tests the bilateral sync payload construction logic in isolation.
    Mirrors the code in sync.py lines 798-836.
    """

    def _build_payload(self, cust_name, cust_email, cust_phone,
                       nh_email_val, nh_phone_val, merged_email="", merged_phone=""):
        """Replicates the bilateral sync logic from sync.py STEP 6."""
        hc_update_payload = {}

        # name: preserve HC name, never overwrite with NetHunt name
        if cust_name and cust_name != "Unknown Customer":
            hc_update_payload["name"] = cust_name

        # email: keep HC email if present, fill from NetHunt if empty
        if not cust_email and nh_email_val:
            merged_email = nh_email_val
        # Always include email if we have a value (HC PUT replaces entire object)
        if merged_email or cust_email:
            hc_update_payload["email"] = merged_email if merged_email else cust_email

        # phone: keep HC phone if present, fill from NetHunt if empty
        if not cust_phone and nh_phone_val:
            merged_phone = nh_phone_val
        # Always include phone if we have a value (HC PUT replaces entire object)
        if merged_phone or cust_phone:
            hc_update_payload["phone"] = merged_phone if merged_phone else cust_phone

        return hc_update_payload

    def test_hc_name_preserved(self):
        payload = self._build_payload("Sania @Clame24", "", "", "", "")
        assert payload["name"] == "Sania @Clame24"

    def test_hc_name_not_overwritten_by_nh(self):
        """Even if NH has 'Назаренко Петро', HC name should be preserved."""
        payload = self._build_payload("Sania @Clame24", "", "", "nazarenko@example.com", "+380226767676")
        assert payload["name"] == "Sania @Clame24"
        assert "Назар" not in payload["name"]

    def test_unknown_customer_name_not_pushed(self):
        payload = self._build_payload("Unknown Customer", "", "", "", "")
        assert "name" not in payload

    def test_hc_email_preserved(self):
        payload = self._build_payload("Test", "hc@example.com", "", "nh@example.com", "")
        assert payload["email"] == "hc@example.com"

    def test_hc_email_empty_filled_from_nh(self):
        payload = self._build_payload("Test", "", "", "nh@example.com", "")
        assert payload["email"] == "nh@example.com"

    def test_hc_phone_preserved(self):
        payload = self._build_payload("Test", "", "+380501112233", "", "+380226767676")
        assert payload["phone"] == "+380501112233"

    def test_hc_phone_empty_filled_from_nh(self):
        payload = self._build_payload("Test", "", "", "", "+380226767676")
        assert payload["phone"] == "+380226767676"

    def test_both_empty_no_phone_email_keys(self):
        payload = self._build_payload("Test", "", "", "", "")
        assert "phone" not in payload
        assert "email" not in payload

    def test_nazarenko_phone_not_overwriting_hc(self):
        """The exact bug: Nazarenko's phone should NOT overwrite HC phone."""
        payload = self._build_payload("Sania", "", "+380509998877", "", "+380226767676")
        assert payload["phone"] == "+380509998877"
        assert "+380226767676" not in payload.get("phone", "")


# ─── 4b. _build_hc_update_payload (real function) ────────────────────────

class TestBuildHcUpdatePayloadReal:
    """Tests that call the actual _build_hc_update_payload from sync.py."""

    @staticmethod
    def _call(cust_name="Test", cust_email="", cust_phone="",
              contact_fields=None, email_nh_key="Email", phone_nh_key="Phone",
              merged_telegram="", telegram_handle="",
              merged_instagram="", instagram_handle="",
              contact_url="http://nh/record/1", contact_name="NH Name",
              contact_id="rec_1", is_new_contact=False,
              customer_data=None, telegram_hc_key="telegram"):
        from backend.services.sync import _build_hc_update_payload
        details_log = []
        payload, eff_email, eff_phone = _build_hc_update_payload(
            cust_name, cust_email, cust_phone,
            contact_fields or {}, email_nh_key, phone_nh_key,
            merged_telegram, telegram_handle,
            merged_instagram, instagram_handle,
            contact_url, contact_name, contact_id, is_new_contact,
            customer_data or {}, telegram_hc_key,
            details_log,
        )
        return payload, eff_email, eff_phone, details_log

    def test_name_preserved_from_hc(self):
        payload, *_ = self._call(cust_name="Sania @Clame24")
        assert payload["name"] == "Sania @Clame24"

    def test_name_not_overwritten_by_nh(self):
        """Even with NH contact_name='Назаренко Петро', HC name wins."""
        payload, *_ = self._call(cust_name="Sania", contact_name="Назаренко Петро")
        assert payload["name"] == "Sania"
        assert "Назар" not in payload["name"]

    def test_unknown_customer_name_not_pushed(self):
        payload, *_ = self._call(cust_name="Unknown Customer")
        assert "name" not in payload

    def test_hc_email_preserved_over_nh(self):
        payload, eff_email, _, _ = self._call(
            cust_email="hc@example.com",
            contact_fields={"Email": ["nh@example.com"]},
        )
        assert payload["email"] == "hc@example.com"
        assert eff_email == "hc@example.com"

    def test_hc_email_empty_filled_from_nh(self):
        payload, eff_email, _, _ = self._call(
            cust_email="",
            contact_fields={"Email": ["nh@example.com"]},
        )
        assert payload["email"] == "nh@example.com"
        assert eff_email == "nh@example.com"

    def test_hc_phone_preserved_over_nh(self):
        payload, _, eff_phone, _ = self._call(
            cust_phone="+380509998877",
            contact_fields={"Phone": ["+380226767676"]},
        )
        assert payload["phone"] == "+380509998877"
        assert "+380226767676" not in payload.get("phone", "")

    def test_hc_phone_empty_filled_from_nh(self):
        payload, _, eff_phone, _ = self._call(
            cust_phone="",
            contact_fields={"Phone": ["+380226767676"]},
        )
        assert payload["phone"] == "+380226767676"
        assert eff_phone == "+380226767676"

    def test_both_empty_no_phone_email(self):
        payload, *_ = self._call(cust_email="", cust_phone="", contact_fields={})
        assert "phone" not in payload
        assert "email" not in payload

    def test_nazarenko_bug_regression(self):
        """The exact bug: two different TG users, NH returns Nazarenko's record."""
        # Sania has his own HC phone, NH has Nazarenko's phone
        payload, eff_email, eff_phone, log = self._call(
            cust_name="Sania @Clame24",
            cust_phone="",
            contact_fields={"Phone": ["+380226767676"], "Email": ["nazarenko@example.com"]},
            contact_name="Назаренко Петро",
            contact_id="rec_nazarenko",
        )
        # Name must be Sania, not Nazarenko
        assert payload["name"] == "Sania @Clame24"
        # Phone from NH fills empty HC phone — but this is the BUG scenario:
        # get_contact should have returned None, so this shouldn't happen in prod
        # Still, the payload logic itself is correct: fill empty from NH
        assert payload["phone"] == "+380226767676"

    def test_notes_always_present(self):
        payload, *_ = self._call(contact_name="Test Contact", contact_id="rec_123")
        assert "notes" in payload
        assert "rec_123" in payload["notes"]

    def test_customdata_merged(self):
        payload, *_ = self._call(
            merged_telegram="sania_tg",
            telegram_handle="",
            customer_data={"customData": [{"property": "existing", "value": "val"}]},
        )
        cd = payload.get("customData", [])
        props = {item.get("property") for item in cd if isinstance(item, dict)}
        assert "existing" in props
        assert "telegram" in props
        assert "nethunt_contact_url1" in props

    def test_customdata_telegram_not_duplicated(self):
        """If telegram_handle already set in HC, don't add it again."""
        payload, *_ = self._call(
            merged_telegram="sania_tg",
            telegram_handle="sania_tg",
        )
        cd = payload.get("customData", [])
        tg_entries = [item for item in cd if isinstance(item, dict) and item.get("property") == "telegram"]
        assert len(tg_entries) == 0  # telegram_handle already set, no update needed


# ─── 5. save_match_link self-healing ──────────────────────────────────────

class TestSaveMatchLink:
    def test_deletes_stale_link(self, tmp_path):
        """save_match_link should delete old NH contact mapping for same HC customer."""
        db_path = str(tmp_path / "test.db")
        with mock_patch("backend.database.DB_PATH", db_path):
            from backend import database
            database.init_db()

            # Insert NH contact rows so the JOIN in find_match_by_hc_customer_id works
            database.save_nh_contact("nh_Nazarenko", "folder1", "Назаренко Петро",
                                     "naz@example.com", "+380226767676", "petro_tg",
                                     "", "", "", "{}")
            database.save_nh_contact("nh_Sania", "folder1", "Саня",
                                     "", "", "Clame24",
                                     "", "", "", "{}")

            # Save initial (corrupted) match: hc_A → nh_Nazarenko
            database.save_match_link("hc_A", "nh_Nazarenko", "chat_link", "high")

            # Verify it exists
            match = database.find_match_by_hc_customer_id("hc_A")
            assert match is not None

            # Save correct match: hc_A → nh_Sania
            database.save_match_link("hc_A", "nh_Sania", "telegram", "high")

            # Verify only the correct match exists
            conn = database.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT nh_contact_id FROM match_links WHERE hc_customer_id = ?", ("hc_A",))
            rows = cursor.fetchall()
            conn.close()

            assert len(rows) == 1
            assert rows[0]["nh_contact_id"] == "nh_Sania"

    def test_same_pair_no_duplicate(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        with mock_patch("backend.database.DB_PATH", db_path):
            from backend import database
            database.init_db()

            database.save_match_link("hc_A", "nh_Sania", "chat_link", "high")
            database.save_match_link("hc_A", "nh_Sania", "telegram", "high")

            conn = database.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as c FROM match_links WHERE hc_customer_id = ?", ("hc_A",))
            count = cursor.fetchone()["c"]
            conn.close()

            assert count == 1


# ─── 6. Local mirror validation logic ─────────────────────────────────────

class TestLocalMirrorValidation:
    """
    Tests the identifier conflict detection logic that was added
    after _search_local_mirror in sync.py.
    """

    def _check_conflict(self, contact, customer_id, merged_telegram,
                        merged_email, merged_phone,
                        hc_id_nh_key="HelpCrunch ID",
                        telegram_nh_key="Telegram",
                        email_nh_key="Email",
                        phone_nh_key="Phone"):
        """Replicates the validation logic from sync.py lines 659-696."""
        def _id_norm(s):
            return re.sub(r'[\s@()+\-.]', '', str(s).strip().lower())

        cf = contact.get("fields", {}) or {}

        def _cf_val(fk):
            raw = cf.get(fk)
            return _id_norm(_first_value(raw)) if raw else ""

        id_conflicts = []
        if hc_id_nh_key:
            nh_cid = _cf_val(hc_id_nh_key)
            if nh_cid and _id_norm(str(customer_id)) and nh_cid != _id_norm(str(customer_id)):
                id_conflicts.append("hc_id")
        if telegram_nh_key and merged_telegram:
            nh_tg = _cf_val(telegram_nh_key)
            if nh_tg and nh_tg != _id_norm(merged_telegram):
                id_conflicts.append("telegram")
        if email_nh_key and merged_email:
            nh_em = _cf_val(email_nh_key)
            if nh_em and nh_em != _id_norm(merged_email):
                id_conflicts.append("email")
        if phone_nh_key and merged_phone:
            nh_ph = _cf_val(phone_nh_key)
            if nh_ph and nh_ph != _id_norm(merged_phone):
                id_conflicts.append("phone")

        return id_conflicts

    def test_nazarenko_match_for_sania_detected_as_conflict(self):
        """Corrupted match: Sania (HC 88888) matched to Nazarenko (NH rec_nazarenko)."""
        conflicts = self._check_conflict(
            contact=NAZARENKO,
            customer_id="88888",  # HC customer is Sania
            merged_telegram="Clame24",
            merged_email="",
            merged_phone="",
        )
        assert "hc_id" in conflicts  # Nazarenko has HC ID 99999, not 88888
        assert "telegram" in conflicts  # Nazarenko has petro_tg, not Clame24

    def test_correct_match_no_conflict(self):
        """Correct match: Sania (HC 88888) matched to Sania (NH rec_sania)."""
        conflicts = self._check_conflict(
            contact=SANIA,
            customer_id="88888",
            merged_telegram="Clame24",
            merged_email="",
            merged_phone="",
        )
        assert conflicts == []

    def test_new_lead_no_conflict_with_empty_fields(self):
        """New lead with empty NH fields should not trigger conflict."""
        empty_contact = {"id": "rec_new", "fields": {}}
        conflicts = self._check_conflict(
            contact=empty_contact,
            customer_id="77777",
            merged_telegram="newguy",
            merged_email="new@example.com",
            merged_phone="+380501112233",
        )
        assert conflicts == []

    def test_phone_format_tolerant_no_conflict(self):
        """Phone with different formatting should not trigger conflict."""
        contact = {"id": "r1", "fields": {"Phone": ["+38 (050) 111-22-33"]}}
        conflicts = self._check_conflict(
            contact=contact,
            customer_id="99999",
            merged_telegram="",
            merged_email="",
            merged_phone="+380501112233",
        )
        assert "phone" not in conflicts


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
