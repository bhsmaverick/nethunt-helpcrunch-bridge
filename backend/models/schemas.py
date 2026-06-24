from pydantic import BaseModel
from typing import Optional


class SettingsUpdate(BaseModel):
    helpcrunch_api_key: str
    helpcrunch_subdomain: str
    helpcrunch_webhook_secret: Optional[str] = ""
    nethunt_api_email: str
    nethunt_api_key: str
    nethunt_contacts_folder: Optional[str] = ""
    nethunt_deals_folder: Optional[str] = ""
    nethunt_base_url: Optional[str] = "https://nethunt.com"
    nethunt_workspace_id: Optional[str] = ""
    sync_priority: Optional[str] = "email,phone,telegram"
    telegram_field_hc: Optional[str] = "telegram"
    telegram_field_nh: Optional[str] = "Telegram"
    instagram_field_nh: Optional[str] = "Instagram"
    name_field_nh: Optional[str] = "Name"
    phone_field_nh: Optional[str] = "Phone"
    email_field_nh: Optional[str] = "Email"
    hc_id_field_nh: Optional[str] = "HelpCrunch ID"
    update_nh_chat_link: Optional[str] = "false"
    nh_chat_link_field: Optional[str] = "HelpCrunch Chat Link"
    utm_source_field_nh: Optional[str] = "utm_source"
    utm_medium_field_nh: Optional[str] = "utm_medium"
    utm_campaign_field_nh: Optional[str] = "utm_campaign"
    utm_term_field_nh: Optional[str] = "utm_term"
    utm_content_field_nh: Optional[str] = "utm_content"
    gclid_field_nh: Optional[str] = "gclid"
    referer_field_nh: Optional[str] = "Referer"
    source_field_nh: Optional[str] = "Source"
    country_field_nh: Optional[str] = "Country"
    city_field_nh: Optional[str] = "City"
    branch_field_nh: Optional[str] = ""
    branch_mapping: Optional[str] = ""


class TestConnectionRequest(BaseModel):
    email: Optional[str] = ""
    key: str
    base_url: Optional[str] = "https://nethunt.com"


class FolderFieldsRequest(BaseModel):
    email: str
    key: str
    base_url: Optional[str] = "https://nethunt.com"
    folder_id: str


class SimulateWebhookRequest(BaseModel):
    event: str
    name: str
    email: str
    phone: str
    telegram: str
    chat_id: Optional[int] = None
    utm_source: Optional[str] = ""
    utm_medium: Optional[str] = ""
    utm_campaign: Optional[str] = ""
    gclid: Optional[str] = ""


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenVerificationRequest(BaseModel):
    username: str
    token: str
