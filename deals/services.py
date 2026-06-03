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
from datetime import datetime, timezone
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

import os
import json
import re
from decimal import Decimal, InvalidOperation
from huggingface_hub import InferenceClient

HF_MODEL = "Qwen/Qwen2.5-7B-Instruct"


def clean_money(value):
    if value in [None, ""]:
        return None

    try:
        cleaned = str(value).replace("$", "").replace(",", "").strip()
        if cleaned.lower() in ["none", "null", "unknown", "n/a"]:
            return None
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def clean_int(value):
    if value in [None, ""]:
        return None

    try:
        cleaned = str(value).replace(",", "").strip()
        if cleaned.lower() in ["none", "null", "unknown", "n/a"]:
            return None
        return int(float(cleaned))
    except Exception:
        return None


def clean_decimal(value):
    if value in [None, ""]:
        return None

    try:
        cleaned = str(value).strip()
        if cleaned.lower() in ["none", "null", "unknown", "n/a"]:
            return None
        return Decimal(cleaned)
    except Exception:
        return None

def extract_property_listings_with_llm(deal):
    hf_token = os.environ.get("HF_TOKEN")

    if not hf_token:
        raise Exception("HF_TOKEN environment variable is missing.")

    client = InferenceClient(
        model=HF_MODEL,
        token=hf_token,
    )

    email_text = f"""
Subject: {deal.subject}
From: {deal.sender}

Body:
{(deal.body or "")[:12000]}
"""

    prompt = f"""
You extract real estate property listings from emails.

The email may contain one property or multiple properties.

Return ONLY valid JSON.

Return this exact structure:

{{
  "has_property_listings": true,
  "listings": [
    {{
      "address": "",
      "zip_code": "",
      "price": null,
      "arv": null,
      "rehab_cost": null,
      "rent": null,
      "taxes": null,
      "beds": null,
      "baths": null,
      "sqft": null,
      "year_built": null,
      "suggested_offer": null,
      "missing_fields": []
    }}
  ]
}}

Rules:
- If multiple properties are in the email, return one object per property.
- Extract ZIP per property if available.
- Do not combine multiple properties into one.
- Use null for missing values.
- Numbers should be plain numbers, not strings.
- If there are no actual property deal listings, return:
{{
  "has_property_listings": false,
  "listings": []
}}

Required fields to look for:
- address/location/zip
- price/list price/asking price
- ARV / after repair value
- rehab cost/repair estimate
- rent estimate
- taxes
- beds
- baths
- square footage
- year built
- suggested offer

Email:
{email_text}
"""

    response = client.chat_completion(
        messages=[
            {
                "role": "system",
                "content": "You return only valid JSON. No markdown. No commentary.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        max_tokens=1800,
        temperature=0.0,
    )

    raw_text = response.choices[0].message["content"]

    print("===== MULTI LISTING LLM RAW START =====")
    print(raw_text)
    print("===== MULTI LISTING LLM RAW END =====")

    try:
        data = json.loads(raw_text)
    except Exception as e:
        print("MULTI LISTING JSON ERROR:", e)
        data = {
            "has_property_listings": False,
            "listings": [],
            "raw_error": raw_text[:1000],
        }

    return data
from .models import PropertyListing


def save_llm_listings_to_db(deal, extracted_data):
    PropertyListing.objects.filter(deal=deal).delete()

    listings = extracted_data.get("listings", [])

    created_listings = []

    for item in listings:
        address = item.get("address", "") or ""
        zip_code = item.get("zip_code", "") or extract_zip_from_address_or_text(
            address,
            deal.body
        )

        listing = PropertyListing.objects.create(
            deal=deal,
            address=address,
            zip_code=zip_code,

            price=clean_money(item.get("price")),
            arv=clean_money(item.get("arv")),
            rehab_cost=clean_money(item.get("rehab_cost")),
            rent=clean_money(item.get("rent")),
            taxes=clean_money(item.get("taxes")),

            beds=clean_decimal(item.get("beds")),
            baths=clean_decimal(item.get("baths")),
            sqft=clean_int(item.get("sqft")),
            year_built=clean_int(item.get("year_built")),
            suggested_offer=clean_money(item.get("suggested_offer")),

            raw_llm_json=item,
        )

        created_listings.append(listing)

    return created_listings
def analyze_property_listing(listing):
    settings = get_settings()

    allowed_zips = get_allowed_zip_list()

    if allowed_zips:
        listing.zip_allowed = listing.zip_code in allowed_zips
    else:
        listing.zip_allowed = True

    if not listing.zip_allowed:
        listing.qualifies = False
        listing.reason = f"ZIP {listing.zip_code or 'missing'} is not allowed."
        listing.save()
        return listing

    # Buy box checks
    if listing.beds is None or listing.beds < Decimal("2"):
        listing.qualifies = False
        listing.reason = "Does not meet minimum beds."
        listing.save()
        return listing

    if listing.baths is None or listing.baths < Decimal("1"):
        listing.qualifies = False
        listing.reason = "Does not meet minimum baths."
        listing.save()
        return listing

    if listing.price is None or listing.price > Decimal("150000"):
        listing.qualifies = False
        listing.reason = "Price missing or above $150,000."
        listing.save()
        return listing

    if listing.year_built is None or listing.year_built < 1970:
        listing.qualifies = False
        listing.reason = "Year built missing or before 1970."
        listing.save()
        return listing

    if listing.sqft is None or listing.sqft < 1300:
        listing.qualifies = False
        listing.reason = "Square footage missing or below 1,300."
        listing.save()
        return listing

    if listing.arv is None:
        listing.qualifies = False
        listing.reason = "Missing ARV. Needs comparable validation."
        listing.save()
        return listing

    rehab = listing.rehab_cost

    if rehab is None:
        rehab = Decimal(listing.sqft) * Decimal("35")
        listing.rehab_cost = rehab

    arv = listing.arv
    price = listing.price

    mao = (arv * Decimal("0.70")) - rehab
    flip_profit = arv - (price + rehab)

    total_investment = price + rehab
    loan = arv * Decimal("0.75")
    brrrr_cash_left = total_investment - loan

    listing.mao = mao
    listing.flip_profit = flip_profit
    listing.brrrr_cash_left = brrrr_cash_left

    listing.qualifies_flip = flip_profit >= Decimal("30000")
    listing.qualifies_brrrr = brrrr_cash_left <= Decimal("5000")
    listing.qualifies = listing.qualifies_flip or listing.qualifies_brrrr

    if listing.qualifies_flip and listing.qualifies_brrrr:
        listing.reason = "Qualifies for Fix & Flip and BRRRR."
    elif listing.qualifies_flip:
        listing.reason = "Qualifies for Fix & Flip."
    elif listing.qualifies_brrrr:
        listing.reason = "Qualifies for BRRRR."
    else:
        listing.reason = "Does not meet flip or BRRRR profit rules."

    listing.save()
    return listing

def get_allowed_zip_list():
    settings = get_settings()

    raw = settings.allowed_zip_codes or ""

    return [
        z.strip()
        for z in raw.replace("\n", ",").split(",")
        if z.strip()
    ]


def extract_zip_from_address_or_text(address, text=""):
    combined = f"{address or ''}\n{text or ''}"
    match = re.search(r"\b\d{5}\b", combined)

    if match:
        return match.group(0)

    return ""
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

from difflib import SequenceMatcher
from .models import Deal


def similarity_score(text1, text2):
    """
    Returns a score from 0.0 to 1.0.
    Higher means more similar.
    """

    text1 = text1 or ""
    text2 = text2 or ""

    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()


def score_email_against_labeled_examples(deal):
    """
    Compares a new email against all labeled emails.

    If it finds a similar labeled email above the confidence threshold,
    it copies that label.

    Example:
    - old labeled email = potential lead
    - new email is 0.74 similar
    - threshold is 0.60
    - new email gets marked as potential lead
    """

    settings = get_settings()

    labeled_examples = Deal.objects.filter(is_labeled=True)

    best_match = None
    best_score = 0

    for example in labeled_examples:
        score = similarity_score(deal.body, example.body)

        if score > best_score:
            best_score = score
            best_match = example

    deal.difflib_score = best_score

    if best_match:
        deal.matched_example_subject = best_match.subject or ""
        deal.matched_example_body = (best_match.body or "")[:2000]

        if best_score >= settings.difflib_confidence_threshold:
            deal.is_potential_lead = best_match.is_potential_lead
            deal.send_to_llm = best_match.is_potential_lead

    deal.save()
    return deal
def extract_message_bodies(payload):
    plain_text = ""
    html_text = ""

    if payload.get("body", {}).get("data"):
        raw = payload["body"]["data"]
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8", errors="ignore")

        if payload.get("mimeType") == "text/html":
            html_text = decoded
            plain_text = BeautifulSoup(decoded, "html.parser").get_text(" ")
        else:
            plain_text = decoded

    for part in payload.get("parts", []):
        part_plain, part_html = extract_message_bodies(part)

        if part_plain and not plain_text:
            plain_text = part_plain

        if part_html and not html_text:
            html_text = part_html

    return plain_text, html_text
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

    SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    ]

    creds = Credentials.from_authorized_user_file(
        "token.json",
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
#Test_mail=get_specific_email("agmusb@gmail.com",5)
#print(Test_mail['body'])
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


from email.utils import parseaddr
from datetime import datetime, timezone


def read_gmail_deals():
    settings = get_settings()
    service = get_gmail_service()

    query = settings.gmail_query or "in:inbox newer_than:1d"

    print("===== READ GMAIL START =====")
    print("QUERY:", query)

    created = []

    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=50
    ).execute()

    messages = result.get("messages", [])

    print("MESSAGES FOUND:", len(messages))

    for msg in messages:
        email_id = msg.get("id")

        print("PROCESSING EMAIL:", email_id)

        try:
            if Deal.objects.filter(email_id=email_id).exists():
                print("SKIPPED DUPLICATE:", email_id)
                continue

            full_msg = service.users().messages().get(
                userId="me",
                id=email_id,
                format="full"
            ).execute()

            payload = full_msg.get("payload", {})
            headers = payload.get("headers", [])

            subject = ""
            sender = ""

            for h in headers:
                name = h.get("name", "").lower()
                value = h.get("value", "")

                if name == "subject":
                    subject = value

                if name == "from":
                    sender = parseaddr(value)[1]

            gmail_received_at = None

            if full_msg.get("internalDate"):
                gmail_received_at = datetime.fromtimestamp(
                    int(full_msg["internalDate"]) / 1000,
                    tz=timezone.utc
                )

            body, html_body = extract_message_bodies(payload)

            # Parser should never be allowed to stop saving emails.
            try:
                parsed = parse_deal_from_text(body)
            except Exception as parse_error:
                print("PARSE ERROR:", parse_error)
                parsed = {}

            if Deal.objects.filter(email_id=email_id).exists():
                print("SKIPPED DUPLICATE:", email_id)
                continue

            deal = Deal.objects.create(
                email_id=email_id,
                sender=sender,
                subject=subject,
                body=body or "",
                html_body=html_body or "",
                raw_email_json=full_msg,
                **parsed
            )

            try:
                analyze_deal(deal)
            except Exception as analysis_error:
                print("ANALYSIS ERROR:", analysis_error)

            try:
                score_email_against_labeled_examples(deal)
            except Exception as score_error:
                print("SCORING ERROR:", score_error)

            try:
                classify_email_yes_no_with_llm(deal)
            except Exception as llm_error:
                print("LLM CLASSIFIER ERROR:", llm_error)

            if deal.llm_is_valid_lead:
                try:
                    process_deal_after_llm_yes(deal)
                except Exception as process_error:
                    print("POST-LLM DEAL PROCESSING ERROR:", process_error)

            created.append(deal)

        except Exception as e:
            print("EMAIL PROCESSING FAILED:", email_id, str(e))

    print("CREATED COUNT:", len(created))
    print("===== READ GMAIL END =====")

    return created
from huggingface_hub import InferenceClient

HF_MODEL = "Qwen/Qwen2.5-7B-Instruct"

import os
def classify_email_yes_no_with_llm(deal):
    """
    First-pass LLM classifier.

    Returns YES only if the email contains ALL required client categories:
    - location
    - price
    - year built
    - square footage
    - ARV / after repair value
    - beds/baths
    - taxes
    - rehab estimate
    - rent estimate
    - suggested offer
    """

    hf_token = os.environ.get("HF_TOKEN")

    if not hf_token:
        raise Exception("HF_TOKEN environment variable is missing.")

    client = InferenceClient(
        model=HF_MODEL,
        token=hf_token
    )
    settings = get_settings()
    required_categories = settings.llm_required_categories.strip()
    email_text = f"""
    Subject: {deal.subject}

    From: {deal.sender}

    Body:
    {deal.body[:8000]}

    """

    prompt = f"""
    You are a strict real estate lead classifier.

    Return YES only if this email contains a valid property listing AND includes ALL of these required categories:

    {required_categories}

    If even one required category is missing, return NO.

    Return ONLY valid JSON exactly like this:

    {{
    "answer": "YES",
    "reason": "short reason",
    "missing_fields": []
    }}

    or:

    {{
    "answer": "NO",
    "reason": "short reason",
    "missing_fields": ["field1", "field2"]
    }}

    Email:
    {email_text}
    """

    response = client.chat_completion(
        messages=[
            {
                "role": "system",
                "content": "You are a strict classifier. Return only valid JSON."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_tokens=300,
        temperature=0.0,
    )

    raw_text = response.choices[0].message["content"]
    print("===== LLM RAW RESPONSE START =====")
    print(raw_text)
    print("===== LLM RAW RESPONSE END =====")

    deal.llm_raw_response = raw_text
    deal.save()
    try:
        data = json.loads(raw_text)
    except Exception:
        data = {
            "answer": "NO",
            "reason": f"Invalid JSON returned by model: {raw_text[:500]}",
            "missing_fields": ["unknown"]
        }

    answer = str(data.get("answer", "NO")).upper().strip()

    deal.llm_checked = True
    deal.llm_is_valid_lead = answer == "YES"
    deal.llm_missing_fields = data.get("missing_fields", [])

    deal.llm_reason = data.get("reason", "")
    deal.save()

    return deal.llm_is_valid_lead
import re
from decimal import Decimal


def extract_zip_from_text(text):
    """
    Finds a 5-digit US ZIP code.
    """
    if not text:
        return ""

    match = re.search(r"\b\d{5}\b", text)

    if match:
        return match.group(0)

    return ""


def get_allowed_zip_list():
    settings = get_settings()

    raw = settings.allowed_zip_codes or ""

    return [
        z.strip()
        for z in raw.replace("\n", ",").split(",")
        if z.strip()
    ]


def check_zip_allowed(deal):
    """
    Uses deal.zip_code if already parsed.
    Otherwise tries to find ZIP inside subject/body.
    """

    zip_code = deal.zip_code or extract_zip_from_text(
        f"{deal.subject}\n{deal.body}"
    )

    deal.zip_code = zip_code

    allowed_zips = get_allowed_zip_list()

    if not allowed_zips:
        deal.zip_allowed = True
    else:
        deal.zip_allowed = zip_code in allowed_zips

    deal.save()

    return deal.zip_allowed
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
def apply_scraped_data_to_deal(deal, scraped_data):
    """
    Only fills missing values.
    Does not overwrite values already found in the email.
    """

    if not scraped_data:
        return deal

    field_map = {
        "arv": "arv",
        "rehab_cost": "rehab_cost",
        "rent": "rent",
        "taxes": "taxes",
        "year_built": "year_built",
        "sqft": "sqft",
        "beds": "beds",
        "baths": "baths",
        "price": "price",
        "zip_code": "zip_code",
        "address": "address",
    }

    for source_key, model_field in field_map.items():
        value = scraped_data.get(source_key)

        if value in [None, ""]:
            continue

        current_value = getattr(deal, model_field, None)

        if current_value in [None, ""]:
            setattr(deal, model_field, value)

    deal.save()
    return deal
def run_client_deal_math(deal):
    """
    Client rules:

    Fix & Flip:
    MAO = (ARV * 0.70) - Repair Costs
    Profit = ARV - (Purchase Price + Repair Costs)
    Qualifies if Profit >= 30000

    BRRRR:
    Total Investment = Purchase Price + Rehab Costs
    Loan = 0.75 * ARV
    Cash Left = Total Investment - Loan
    Qualifies if Cash Left <= 5000
    """

    settings = get_settings()

    deal.math_checked = True

    if not deal.zip_allowed:
        deal.math_qualifies = False
        deal.math_reason = "ZIP code is not in allowed list."
        deal.save()
        return False

    if not deal.price or not deal.arv:
        deal.math_qualifies = False
        deal.math_reason = "Missing price or ARV."
        deal.save()
        return False

    rehab_cost = deal.rehab_cost

    if not rehab_cost:
        if deal.sqft:
            rehab_cost = Decimal(deal.sqft) * Decimal(settings.rehab_cost_per_sqft)
            deal.rehab_cost = rehab_cost
        else:
            deal.math_qualifies = False
            deal.math_reason = "Missing rehab cost and square footage."
            deal.save()
            return False

    arv = Decimal(deal.arv)
    price = Decimal(deal.price)
    rehab = Decimal(rehab_cost)

    flip_multiplier = Decimal(str(settings.flip_arv_multiplier or 0.70))
    loan_multiplier = Decimal(str(settings.brrrr_loan_multiplier or 0.75))

    mao = (arv * flip_multiplier) - rehab
    flip_profit = arv - (price + rehab)

    total_investment = price + rehab
    loan = loan_multiplier * arv
    brrrr_cash_left = total_investment - loan

    deal.mao = mao
    deal.flip_profit = flip_profit
    deal.brrrr_cash_left = brrrr_cash_left

    flip_ok = flip_profit >= Decimal(settings.min_flip_profit or 30000)
    brrrr_ok = brrrr_cash_left <= Decimal(settings.max_brrrr_cash_left or 5000)

    if flip_ok and brrrr_ok:
        deal.math_qualifies = True
        deal.qualifies = True
        deal.recommendation = "Qualifies for Fix & Flip and BRRRR."
    elif flip_ok:
        deal.math_qualifies = True
        deal.qualifies = True
        deal.recommendation = "Qualifies for Fix & Flip."
    elif brrrr_ok:
        deal.math_qualifies = True
        deal.qualifies = True
        deal.recommendation = "Qualifies for BRRRR."
    else:
        deal.math_qualifies = False
        deal.qualifies = False
        deal.recommendation = "Does not qualify."

    deal.math_reason = deal.recommendation
    deal.save()

    return deal.math_qualifies
def process_deal_after_llm_yes(deal):
    extracted_data = extract_property_listings_with_llm(deal)

    listings = save_llm_listings_to_db(deal, extracted_data)

    qualified_listings = []

    for listing in listings:
        analyze_property_listing(listing)

        if listing.qualifies:
            qualified_listings.append(listing)

    if qualified_listings:
        deal.qualifies = True
        deal.recommendation = f"{len(qualified_listings)} listing(s) qualify."
    else:
        deal.qualifies = False
        deal.recommendation = "No listings qualified."

    deal.save()

    for listing in qualified_listings:
        message = (
            f"Qualified deal found!\n"
            f"Address: {listing.address}\n"
            f"ZIP: {listing.zip_code}\n"
            f"Price: ${listing.price}\n"
            f"ARV: ${listing.arv}\n"
            f"Rehab: ${listing.rehab_cost}\n"
            f"MAO: ${listing.mao}\n"
            f"Flip Profit: ${listing.flip_profit}\n"
            f"BRRRR Cash Left: ${listing.brrrr_cash_left}\n"
            f"Reason: {listing.reason}"
        )

        try:
            send_whatsapp_message(message)
        except Exception as e:
            print("WHATSAPP SEND ERROR:", e)

    return qualified_listings