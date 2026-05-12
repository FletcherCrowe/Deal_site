"""
services.py

This file contains the actual backend automation logic.

It handles:

1. Loading editable app settings
2. Parsing deal info from email text
3. Running Fix & Flip / BRRRR calculations
4. Connecting to Gmail
5. Reading deal emails
6. Sending WhatsApp Cloud API messages

Keeping this logic in services.py makes the Django views cleaner.
"""

import base64
import re
from email.utils import parseaddr

import requests
from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .models import AppSettings, Deal,EmailReadLog
import json
from .models import AppSettings, Deal, GmailAccount

# Gmail API permissions.
#
# gmail.readonly = read emails.
# gmail.send = send emails later when you add auto-reply.
#
# If you only want to read emails for now, you can remove gmail.send.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_settings():
    """
    Get the one AppSettings record.

    This project is meant for one client/dashboard at first.

    If no settings row exists yet, create one with default values.
    """

    settings = AppSettings.objects.first()

    if not settings:
        settings = AppSettings.objects.create()

    return settings


def clean_money(value):
    """
    Convert a money string into an integer.

    Examples:
    "$150,000" -> 150000
    "150,000" -> 150000
    "150000" -> 150000

    If the value is missing, return None.
    """

    if not value:
        return None

    cleaned = re.sub(r"[^\d]", "", value)

    if not cleaned:
        return None

    return int(cleaned)


def parse_deal_from_text(text):
    """
    Extract property data from an email body.

    This is a simple regex-based parser for Milestone 1.

    It looks for:
    - price
    - beds
    - baths
    - sqft
    - year built
    - ARV
    - rehab
    - rent
    - zip code

    Later, you can replace or improve this with:
    - OpenAI extraction
    - Claude extraction
    - custom templates per wholesaler
    - better address parsing
    """

    data = {}

    # Search examples:
    # Price: $120,000
    # Asking Price $120,000
    # Purchase Price: 120000
    price_match = re.search(
        r"(price|asking|purchase price)\D{0,30}\$?([\d,]+)",
        text,
        re.I
    )

    # Search examples:
    # 3 beds
    # 3 bed
    # 3 bd
    beds_match = re.search(
        r"(\d+(\.\d+)?)\s*(bed|beds|bd)",
        text,
        re.I
    )

    # Search examples:
    # 2 baths
    # 2 bath
    # 2 ba
    baths_match = re.search(
        r"(\d+(\.\d+)?)\s*(bath|baths|ba)",
        text,
        re.I
    )

    # Search examples:
    # 1300 sqft
    # 1,300 sq ft
    # 1300 square feet
    sqft_match = re.search(
        r"([\d,]+)\s*(sqft|sq ft|square feet)",
        text,
        re.I
    )

    # Search examples:
    # Year Built: 1985
    # Built: 1985
    year_match = re.search(
        r"(built|year built)\D{0,30}(\d{4})",
        text,
        re.I
    )

    # Search examples:
    # ARV: $220,000
    # arv 220000
    arv_match = re.search(
        r"arv\D{0,30}\$?([\d,]+)",
        text,
        re.I
    )

    # Search examples:
    # Rehab: $45,000
    # Rehab cost 45000
    rehab_match = re.search(
        r"rehab\D{0,30}\$?([\d,]+)",
        text,
        re.I
    )

    # Search examples:
    # Rent: $1,400
    # rent estimate 1400
    rent_match = re.search(
        r"rent\D{0,30}\$?([\d,]+)",
        text,
        re.I
    )

    # Simple zip code extraction.
    zip_match = re.search(r"\b\d{5}\b", text)

    data["price"] = clean_money(price_match.group(2)) if price_match else None
    data["beds"] = float(beds_match.group(1)) if beds_match else None
    data["baths"] = float(baths_match.group(1)) if baths_match else None
    data["sqft"] = clean_money(sqft_match.group(1)) if sqft_match else None
    data["year_built"] = int(year_match.group(2)) if year_match else None
    data["arv"] = clean_money(arv_match.group(1)) if arv_match else None
    data["rehab_cost"] = clean_money(rehab_match.group(1)) if rehab_match else None
    data["rent"] = clean_money(rent_match.group(1)) if rent_match else None
    data["zip_code"] = zip_match.group(0) if zip_match else ""

    return data


def analyze_deal(deal):
    """
    Run the client's deal rules.

    Current formulas:

    If rehab is missing:
        rehab_cost = sqft * rehab_cost_per_sqft

    MAO:
        MAO = (ARV * 0.70) - rehab_cost

    Flip profit:
        profit = ARV - (purchase_price + rehab_cost)

    BRRRR:
        total_investment = purchase_price + rehab_cost
        loan = ARV * 0.75
        cash_left = total_investment - loan

    Deal qualifies if:
        it passes buy box
        AND
        it passes either Fix & Flip or BRRRR
    """

    settings = get_settings()

    # Auto-calculate rehab if missing.
    if not deal.rehab_cost and deal.sqft:
        deal.rehab_cost = deal.sqft * settings.rehab_cost_per_sqft

    # Make sure we have the core numbers needed.
    if not deal.arv or not deal.price or not deal.rehab_cost:
        deal.qualifies = False
        deal.recommendation = "Missing ARV, price, or rehab cost."
        deal.save()
        return deal

    # Maximum Allowable Offer.
    deal.mao = int((deal.arv * settings.flip_arv_multiplier) - deal.rehab_cost)

    # BRRRR numbers.
    total_investment = deal.price + deal.rehab_cost
    loan = deal.arv * settings.brrrr_loan_multiplier

    # Profit/cash-left calculations.
    deal.flip_profit = int(deal.arv - total_investment)
    deal.brrrr_cash_left = int(total_investment - loan)

    # Convert comma-separated zips into a Python list.
    allowed_zips = [
        z.strip()
        for z in settings.allowed_zip_codes.split(",")
        if z.strip()
    ]

    # Buy box rule check.
    passes_buy_box = (
        (deal.beds or 0) >= settings.min_beds and
        (deal.baths or 0) >= settings.min_baths and
        (deal.price or 999999999) <= settings.max_price and
        (deal.year_built or 0) >= settings.min_year_built and
        (deal.sqft or 0) >= settings.min_sqft and
        (not allowed_zips or deal.zip_code in allowed_zips)
    )

    # Strategy checks.
    passes_flip = deal.flip_profit >= settings.min_flip_profit
    passes_brrrr = deal.brrrr_cash_left <= settings.max_brrrr_cash_left

    # Final qualification.
    deal.qualifies = passes_buy_box and (passes_flip or passes_brrrr)

    # Human-readable recommendation for dashboard.
    if deal.qualifies:
        if passes_flip and passes_brrrr:
            deal.recommendation = "Good deal. Qualifies for both Fix & Flip and BRRRR."
        elif passes_flip:
            deal.recommendation = "Good deal. Qualifies as Fix & Flip."
        else:
            deal.recommendation = "Good deal. Qualifies as BRRRR."
    else:
        deal.recommendation = "Rejected. Does not meet current buy box/formula rules."

    deal.save()
    return deal


import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from .models import GmailAccount


def get_gmail_service():
    account = GmailAccount.objects.first()

    if not account:
        raise Exception("No Gmail account connected yet.")

    creds = Credentials.from_authorized_user_info(
        account.token_json,
        SCOPES
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        account.token_json = json.loads(creds.to_json())
        account.save()

    return build("gmail", "v1", credentials=creds)
def get_specific_email(sender_email, email_number):
    """
    Get data from a specific email from a sender.

    Example:
        get_specific_email("gmusb@gmail.com", 4)

    -> Gets the 4th newest email from that sender
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    creds = Credentials.from_authorized_user_file(
        "token.json",
        SCOPES
    )

    service = build("gmail", "v1", credentials=creds)

    # Search emails from sender
    #reults is just going to be the ID
    results = service.users().messages().list(
        userId="me",
        q=f"from:{sender_email}",

    ).execute()
    
    messages = results.get("messages", [])

    # Get the specific email
    target_email = messages[email_number - 1]
    message = service.users().messages().get(
        userId="me",
        id=target_email["id"],
        format="full"
    ).execute()
    payload = message["payload"]
    headers = payload["headers"]
    subject = ""
    sender = ""
    date = ""

    for header in headers:

        if header["name"] == "Subject":
            subject = header["value"]

        elif header["name"] == "From":
            sender = header["value"]

        elif header["name"] == "Date":
            date = header["value"]

    # Get email text
    data = None

    parts = payload.get("parts")

    if parts:
        for part in parts:
            if part["mimeType"] == "text/plain":
                data = part["body"].get("data")
                break
    else:
        data = payload["body"].get("data")

    body_text = ""

    if data:
        body_text = base64.urlsafe_b64decode(
            data
        ).decode("utf-8")

    return {
        "id": target_email["id"],
        "subject": subject,
        "from": sender,
        "date": date,
        "body": body_text
    }
Test_mail=get_specific_email("agmusb@gmail.com",5)
print(Test_mail['body'])
def extract_message_body(payload):
    """
    Extract readable email body text from a Gmail API message payload.

    Gmail messages can be:
    - plain text
    - HTML
    - multipart with nested parts

    This function tries to find text/plain or text/html.
    If HTML is found, BeautifulSoup converts it into plain text.
    """
    
    parts = payload.get("parts", [])

    # Simple email body.
    if "body" in payload and payload["body"].get("data"):
        raw = payload["body"]["data"]
        return base64.urlsafe_b64decode(raw).decode("utf-8", errors="ignore")

    # Multipart email body.
    for part in parts:
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")

        if body_data and mime_type in ["text/plain", "text/html"]:
            decoded = base64.urlsafe_b64decode(body_data).decode(
                "utf-8",
                errors="ignore"
            )

            if mime_type == "text/html":
                decoded = BeautifulSoup(decoded, "html.parser").get_text(" ")

            return decoded

        # Gmail parts can be nested.
        nested = extract_message_body(part)

        if nested:
            return nested

    return ""


def read_gmail_deals():
    """
    Reads Gmail messages, logs every email it sees, and catches errors
    so one bad email does not crash the whole system.
    """

    settings = get_settings()
    service = get_gmail_service()
    created = []
    

    try:
        result = service.users().messages().list(
            userId="me",
            maxResults=20
        ).execute()

        messages = result.get("messages", [])
        labels=result.get("labels", [])
        print(f'labels: {labels}')
    except Exception as e:
        EmailReadLog.objects.create(
            status="gmail_search_error",
            error_message=str(e)
        )
        return created
    print(f'Messages: {messages}')
    for msg in messages:
        email_id = msg.get("id", "")
        
        try:
            if Deal.objects.filter(email_id=None).exists():
                EmailReadLog.objects.create(
                    email_id=email_id,
                    status="skipped_duplicate",
                    error_message="Email was already processed."
                )
                continue

            full_msg = service.users().messages().get(
                userId="me",
                id=email_id,
                format="full"
            ).execute()
            payload = full_msg.get("payload", {})
            headers = payload.get("headers", [])
            snippet = full_msg.get("snippet", "")

            subject = ""
            sender = ""

            for h in headers:
                name = h.get("name", "").lower()
                value = h.get("value", "")

                if name == "subject":
                    subject = value

                if name == "from":
                    sender = parseaddr(value)[1]

            body = extract_message_body(payload)
            parsed = parse_deal_from_text(body)

            deal = Deal.objects.create(
                email_id=email_id,
                sender=sender,
                subject=subject,
                body=body,
                raw_email_json=full_msg,
                **parsed
            )
            analyze_deal(deal)
            if Deal.objects.filter(email_id=email_id).exists():
                print("SKIPPED DUPLICATE:", email_id)
                continue
            

            EmailReadLog.objects.create(
                email_id=email_id,
                sender=sender,
                subject=subject,
                snippet=snippet,
                status="processed",
                deal_created=True,
                qualifies=deal.qualifies
            )

            created.append(deal)

        except Exception as e:
            EmailReadLog.objects.create(
                email_id=email_id,
                status="email_processing_error",
                error_message=str(e)
            )
    print("created")
    print(created)
    return created


def send_whatsapp_message(message):
    """
    Send a WhatsApp message using Meta WhatsApp Cloud API.

    Required settings:
    - whatsapp_token
    - whatsapp_phone_number_id
    - whatsapp_to_number

    Phone number format should usually be country code + number.
    Example:
        15551234567

    This is NOT Twilio.
    This talks directly to Meta's Graph API.
    """

    settings = get_settings()

    if not settings.whatsapp_token:
        return 400, "Missing WhatsApp token."

    if not settings.whatsapp_phone_number_id:
        return 400, "Missing WhatsApp phone number ID."

    if not settings.whatsapp_to_number:
        return 400, "Missing WhatsApp recipient number."

    url = f"https://graph.facebook.com/v20.0/{settings.whatsapp_phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": settings.whatsapp_to_number,
        "type": "text",
        "text": {
            "body": message
        }
    }

    response = requests.post(url, headers=headers, json=payload)

    return response.status_code, response.text