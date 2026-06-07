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

def clean_int(value):
    if value in [None, ""]:
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    value = str(value).strip()

    if value.lower() in ["none", "null", "unknown", "n/a"]:
        return None

    cleaned = re.sub(r"[^\d.]", "", value)

    if not cleaned:
        return None

    try:
        return int(float(cleaned))
    except Exception:
        return None
def clean_money(value):
    """
    Converts money values into Decimal.

    Handles:
    - 295000
    - "295000"
    - "$295,000"
    - "$225,000 - $235,000"
    - "ZERO; MOVE IN READY"
    - None
    """

    if value in [None, ""]:
        return None

    # If LLM already returned a number, handle it directly.
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))

    value = str(value).strip()

    if value.lower() in ["none", "null", "unknown", "n/a"]:
        return None

    if "zero" in value.lower():
        return Decimal("0")

    # If value is a range, use lower number.
    # Example: "$225,000 - $235,000"
    if "-" in value:
        value = value.split("-")[0].strip()

    # Remove everything except digits and decimal point.
    cleaned = re.sub(r"[^\d.]", "", value)

    if not cleaned:
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
def clean_decimal(value):
    if value in [None, ""]:
        return None

    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))

    value = str(value).strip()

    if value.lower() in ["none", "null", "unknown", "n/a"]:
        return None

    cleaned = re.sub(r"[^\d.]", "", value)

    if not cleaned:
        return None

    try:
        return Decimal(cleaned)
    except Exception:
        return None
def extract_property_listings_with_llm(deal):
    email_text = str(deal.body or "")

    all_zips = find_all_zip_codes(email_text)
    property_blocks = split_email_into_property_blocks(email_text)

    print("ALL ZIP CODES FOUND:", all_zips)
    print("PROPERTY BLOCKS FOUND:", len(property_blocks))

    listings = []

    # Best path: multiple detected property blocks
    if property_blocks:
        for block in property_blocks:
            try:
                listing_data = extract_single_listing_with_llm(block)
                listings.append(listing_data)
            except Exception as e:
                print("SINGLE LISTING EXTRACTION ERROR:", e)

                listings.append({
                    "address": block.get("address", ""),
                    "zip_code": block.get("zip_code", ""),
                    "price": None,
                    "arv": None,
                    "rehab_cost": None,
                    "rent": None,
                    "taxes": None,
                    "beds": None,
                    "baths": None,
                    "sqft": None,
                    "year_built": None,
                    "suggested_offer": None,
                    "missing_fields": ["single_listing_extraction_failed"],
                })

        return {
            "has_property_listings": len(listings) > 0,
            "all_zip_codes_found": all_zips,
            "listings": listings,
        }

    # Fallback: no address blocks detected, use original whole-email LLM extraction
    hf_token = os.environ.get("HF_TOKEN")

    if not hf_token:
        raise Exception("HF_TOKEN environment variable is missing.")

    client = InferenceClient(
        model=HF_MODEL,
        token=hf_token,
    )

    prompt = f"""
You extract real estate property listings from emails.

The email may contain one property or many properties.

Return ONLY valid JSON. No markdown.

Use this exact structure:

{{
  "has_property_listings": true,
  "all_zip_codes_found": [],
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
- Return one object per property.
- Do not combine multiple properties.
- Use null for missing values.
- Numbers should be plain numbers.
- If a value is a range like "$225,000 - $235,000", use the lower number.
- Extract all ZIP codes and put them in all_zip_codes_found.

Email:
{email_text[:14000]}
"""

    response = client.chat_completion(
        messages=[
            {
                "role": "system",
                "content": "Return only valid JSON. No markdown. No explanation.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        max_tokens=2200,
        temperature=0.0,
    )

    raw_text = response.choices[0].message["content"]

    print("===== WHOLE EMAIL MULTI LISTING RAW =====")
    print(raw_text)

    try:
        data = json.loads(raw_text)
    except Exception:
        data = {
            "has_property_listings": False,
            "all_zip_codes_found": all_zips,
            "listings": [],
            "raw_error": raw_text[:1000],
        }

    if not data.get("all_zip_codes_found"):
        data["all_zip_codes_found"] = all_zips

    return data
from .models import PropertyListing


def save_llm_listings_to_db(deal, extracted_data):
    PropertyListing.objects.filter(deal=deal).delete()

    listings = extracted_data.get("listings", [])
    created_listings = []

    for item in listings:
        address = str(item.get("address") or "").strip()
        city = str(item.get("city") or "").strip()
        state = str(item.get("state") or "").strip()
        zip_code = normalize_zip(item.get("zip_code"))

        full_address = address

        if city and city.lower() not in full_address.lower():
            full_address = f"{full_address}, {city}"

        if state and state.lower() not in full_address.lower():
            full_address = f"{full_address}, {state}"

        if zip_code and zip_code not in full_address:
            full_address = f"{full_address} {zip_code}"

        price = (
            item.get("asking_price")
            or item.get("list_price")
            or item.get("purchase_price")
            or item.get("price")
        )

        rent = (
            item.get("rent_high")
            or item.get("rent_low")
            or item.get("rent")
        )

        suggested_offer = (
            item.get("suggested_offer_low")
            or item.get("suggested_offer")
        )

        listing = PropertyListing.objects.create(
            deal=deal,
            address=full_address.strip(),
            zip_code=zip_code,

            price=clean_money(price),
            arv=clean_money(item.get("arv")),
            rehab_cost=clean_money(item.get("rehab_cost")),
            rent=clean_money(rent),
            taxes=clean_money(item.get("taxes")),

            beds=clean_decimal(item.get("beds")),
            baths=clean_decimal(item.get("baths")),
            sqft=clean_int(item.get("sqft")),
            year_built=clean_int(item.get("year_built")),
            suggested_offer=clean_money(suggested_offer),

            raw_llm_json=item,
        )

        created_listings.append(listing)

    return created_listings
def extract_beds_baths_sqft_from_text(text):
    """
    Handles patterns like:
    Bed/Bath & SQFT: 4/3 & 2,778
    Beds/Baths/Sqft: 3/2 & 1,248
    4 bed 3 bath 2,778 sqft
    """

    text = str(text or "")

    # Pattern: Bed/Bath & SQFT: 4/3 & 2,778
    match = re.search(
        r"bed\s*/?\s*bath\s*&?\s*sqft\s*:\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*&\s*([\d,]+)",
        text,
        re.IGNORECASE
    )

    if match:
        return {
            "beds": match.group(1),
            "baths": match.group(2),
            "sqft": match.group(3),
        }

    # Pattern: 4/3 & 2,778
    match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*&\s*([\d,]+)\b",
        text,
        re.IGNORECASE
    )

    if match:
        return {
            "beds": match.group(1),
            "baths": match.group(2),
            "sqft": match.group(3),
        }

    # Pattern: 4 bed 3 bath 2778 sqft
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*bed[s]?\D+(\d+(?:\.\d+)?)\s*bath[s]?\D+([\d,]+)\s*sqft",
        text,
        re.IGNORECASE
    )

    if match:
        return {
            "beds": match.group(1),
            "baths": match.group(2),
            "sqft": match.group(3),
        }

    return {
        "beds": None,
        "baths": None,
        "sqft": None,
    }
def analyze_property_listing(listing):
    settings = get_settings()

    allowed_zips = get_allowed_zip_list()

    listing.zip_code = normalize_zip(listing.zip_code)

    print("========== ZIP CHECK ==========")
    print("LISTING ADDRESS:", listing.address)
    print("LISTING ZIP:", repr(listing.zip_code))
    print("ALLOWED ZIPS COUNT:", len(allowed_zips))
    print("ALLOWED ZIPS SAMPLE:", allowed_zips[:10])
    print("ZIP IN ALLOWED:", listing.zip_code in allowed_zips)
    print("===============================")

    listing.zip_allowed = listing.zip_code in allowed_zips
    listing.save()
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

DEFAULT_ALLOWED_ZIPS = """
38002
38016
38017
38018
38027
38053
38104
38105
38106
38107
38108
38109
38111
38112
38114
38115
38116
38117
38118
38119
38122
38125
38126
38127
38128
38133
38134
38135
38138
38139
38141
38637
38654
38671
38672
"""


def get_allowed_zip_list():
    """
    Gets allowed zip codes from AppSettings.
    Falls back to the client's default zip list if settings are blank.
    """

    settings = get_settings()

    raw = getattr(settings, "allowed_zip_codes", "") or ""

    # fallback so the system still works if settings box is blank
    if not str(raw).strip():
        raw = DEFAULT_ALLOWED_ZIPS

    raw = str(raw)

    allowed = set()

    # Find every 5 digit zip in the settings text
    for zip_code in re.findall(r"\b\d{5}\b", raw):
        allowed.add(str(zip_code).strip())

    return sorted(allowed)
def normalize_zip(value):
    """
    Converts zip to clean 5-digit string.
    """

    value = str(value or "").strip()

    match = re.search(r"\b\d{5}\b", value)

    if match:
        return match.group(0)

    return ""
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
        maxResults=10
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
            deal.refresh_from_db()
            if deal.llm_is_valid_lead:
                try:
                    process_deal_after_llm_yes(deal)
                except Exception as process_error:
                    print("POST-LLM MULTI LISTING ERROR:", process_error)

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
    """
    Runs after Hugging Face classifier says YES.

    Gemini extracts listings.
    Then Python checks each listing.
    Gemini comp search only runs on listings that pass basic filters.
    """

    extracted_data = extract_property_listings_with_gemini(deal)

    listings = save_llm_listings_to_db(deal, extracted_data)

    qualified = []

    for listing in listings:
        # First run basic ZIP/buy-box/math using email ARV.
        analyze_property_listing(listing)

        # Only do web comp search if it has a valid ZIP and basic property info.
        # This saves money and avoids comp searching junk.
        if (
            listing.zip_allowed
            and listing.price
            and listing.beds
            and listing.baths
            and listing.sqft
        ):
            try:
                validate_arv_with_gemini_comps(listing)

                # Re-run math after ARV validation.
                analyze_property_listing(listing)

            except Exception as comp_error:
                print("GEMINI COMP VALIDATION ERROR:", comp_error)

        if listing.qualifies:
            qualified.append(listing)

    if qualified:
        deal.qualifies = True
        deal.recommendation = f"{len(qualified)} listing(s) qualify."
    else:
        deal.qualifies = False
        deal.recommendation = "No listings qualified."

    deal.save()

    for listing in qualified:
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

    return qualified


ADDRESS_ZIP_PATTERN = re.compile(
    r"(?P<address>\d{1,6}\s+[^\n,]+(?:,\s*[^\n,]+)*,\s*(?:TN|MS)\s+(?P<zip>38\d{3}))",
    re.IGNORECASE
)


def find_all_zip_codes(text):
    text = str(text or "")
    zips = re.findall(r"\b38\d{3}\b", text)
    return list(dict.fromkeys(zips))


def extract_zip_from_address_or_text(address="", text=""):
    combined = f"{str(address or '')}\n{str(text or '')}"
    match = re.search(r"\b38\d{3}\b", combined)

    if match:
        return match.group(0)

    return ""


def split_email_into_property_blocks(text):
    text = str(text or "")

    matches = list(ADDRESS_ZIP_PATTERN.finditer(text))
    blocks = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)

        block_text = str(text[start:end] or "").strip()

        blocks.append({
            "address": str(match.group("address") or "").strip(),
            "zip_code": str(match.group("zip") or "").strip(),
            "text": block_text,
        })

    return blocks


def split_email_into_property_blocks(text):
    """
    Splits one email into multiple property blocks using address + ZIP lines.

    Example match:
    320 S Yates Rd, Memphis, TN 38120
    """

    if not text:
        return []

    matches = list(ADDRESS_ZIP_PATTERN.finditer(text))

    blocks = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)

        block_text = text[start:end].strip()

        blocks.append({
            "address": match.group("address").strip(),
            "zip_code": match.group("zip").strip(),
            "text": block_text,
        })

    return blocks
def extract_single_listing_with_llm(block):
    hf_token = os.environ.get("HF_TOKEN")

    if not hf_token:
        raise Exception("HF_TOKEN environment variable is missing.")

    client = InferenceClient(
        model=HF_MODEL,
        token=hf_token,
    )

    prompt = f"""
You extract ONE real estate property listing.

Return ONLY valid JSON. No markdown.

Use this exact structure:

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

Rules:
- Extract only the property in this text block.
- Use null for missing values.
- If rehab says ZERO or MOVE IN READY, use 0.
- If a value is a range like "$225,000 - $235,000", use the lower number.
- Numbers should be plain numbers, not strings.

Property block:
{block["text"][:5000]}
"""

    response = client.chat_completion(
        messages=[
            {
                "role": "system",
                "content": "Return only valid JSON. No markdown. No explanation.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        max_tokens=700,
        temperature=0.0,
    )

    raw_text = response.choices[0].message["content"]

    print("===== SINGLE LISTING LLM RAW =====")
    print(raw_text)

    try:
        data = json.loads(raw_text)
    except Exception:
        data = {
            "address": block.get("address", ""),
            "zip_code": block.get("zip_code", ""),
            "price": None,
            "arv": None,
            "rehab_cost": None,
            "rent": None,
            "taxes": None,
            "beds": None,
            "baths": None,
            "sqft": None,
            "year_built": None,
            "suggested_offer": None,
            "missing_fields": ["json_parse_failed"],
            "raw_error": raw_text[:1000],
        }
    fallback_bbs = extract_beds_baths_sqft_from_text(block.get("text", ""))

    if data.get("beds") in [None, "", "unknown", "N/A"]:
        data["beds"] = fallback_bbs.get("beds")

    if data.get("baths") in [None, "", "unknown", "N/A"]:
        data["baths"] = fallback_bbs.get("baths")

    if data.get("sqft") in [None, "", "unknown", "N/A"]:
        data["sqft"] = fallback_bbs.get("sqft")
    if not data.get("address"):
        data["address"] = block.get("address", "")

    if not data.get("zip_code"):
        data["zip_code"] = block.get("zip_code", "")

    return data
import os
import json
import re
from decimal import Decimal, InvalidOperation

from google import genai
from google.genai import types

from .models import PropertyListing, PropertyComp
GEMINI_EXTRACTION_MODEL = "gemini-2.5-flash"
GEMINI_COMPS_MODEL = "gemini-2.5-flash"
def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise Exception("GEMINI_API_KEY environment variable is missing.")

    return genai.Client(api_key=api_key)


def parse_json_safely(raw_text):
    """
    Handles clean JSON and occasional ```json wrappers.
    """

    raw_text = str(raw_text or "").strip()

    raw_text = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(raw_text)
    except Exception:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)

        if match:
            return json.loads(match.group(0))

    raise ValueError(f"Could not parse JSON: {raw_text[:500]}")
def extract_property_listings_with_gemini(deal):
    """
    Second-stage extractor.

    This runs only after Hugging Face says the email is a valid deal email.
    It extracts every property listing separately.
    """

    client = get_gemini_client()

    email_text = str(deal.body or "")

    prompt = f"""
You are an expert real estate deal extraction engine.

This email may contain one property listing or many property listings.

Your job:
Extract EACH property as a separate object.
Do not combine properties.
Do not skip expensive properties.
Do not decide if a deal qualifies.
Extraction only.

Return ONLY valid JSON.

Required JSON structure:

{{
  "has_property_listings": true,
  "listings": [
    {{
      "address": "",
      "city": "",
      "state": "",
      "zip_code": "",
      "type_of_listing": "",
      "asking_price": null,
      "list_price": null,
      "purchase_price": null,
      "arv": null,
      "rehab_cost": null,
      "rent_low": null,
      "rent_high": null,
      "taxes": null,
      "insurance": null,
      "beds": null,
      "baths": null,
      "sqft": null,
      "year_built": null,
      "suggested_offer_low": null,
      "suggested_offer_high": null,
      "estimated_profit_low": null,
      "estimated_profit_high": null,
      "missing_fields": []
    }}
  ]
}}

Rules:
- Return one listing object per property address.
- If the email has 18 properties, return 18 listing objects.
- Use null for missing values.
- Convert money to plain numbers.
- Convert "ZERO", "ZERO; MOVE IN READY", or "MOVE IN READY" rehab to 0.
- For "Bed/Bath & SQFT: 4/3 & 2,778", return beds=4, baths=3, sqft=2778.
- For "Market Rent: $1,200-$1,800", return rent_low=1200 and rent_high=1800.
- For "Suggested Offer Amount: $225,000 - $235,000", return suggested_offer_low=225000 and suggested_offer_high=235000.
- ZIP code must belong to the specific listing.
- Do not use street numbers as ZIP codes.
- If no property listings exist, return:
{{
  "has_property_listings": false,
  "listings": []
}}

Email:
{email_text[:60000]}
"""

    response = client.models.generate_content(
        model=GEMINI_EXTRACTION_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )

    raw_text = response.text

    print("===== GEMINI EXTRACTION RAW START =====")
    print(raw_text)
    print("===== GEMINI EXTRACTION RAW END =====")

    try:
        return parse_json_safely(raw_text)
    except Exception as e:
        print("GEMINI EXTRACTION JSON ERROR:", e)
        return {
            "has_property_listings": False,
            "listings": [],
            "raw_error": raw_text[:2000],
        }
def find_comps_with_gemini(listing):
    """
    Uses Gemini + Google Search grounding to look for relevant sold comps.

    Important:
    This should be treated as a research/helper step.
    Python should still do the final ARV/math.
    """

    client = get_gemini_client()

    prompt = f"""
You are helping validate ARV for a real estate investment property.

Subject property:
Address: {listing.address}
ZIP: {listing.zip_code}
Beds: {listing.beds}
Baths: {listing.baths}
Sqft: {listing.sqft}
Year built: {listing.year_built}
Email-provided ARV: {listing.arv}

Find up to 5 relevant comparable sold properties using public web results.

Comp filters:
- Same neighborhood or nearby preferred
- Similar square footage, ideally within ±20%
- Similar bed/bath count
- Sold in the last 6–12 months preferred
- Prefer actual sold properties, not active listings
- Include source URLs when available

Return ONLY valid JSON:

{{
  "comps_found": true,
  "comps": [
    {{
      "address": "",
      "sold_price": null,
      "beds": null,
      "baths": null,
      "sqft": null,
      "sold_date": "",
      "distance_miles": null,
      "source_url": "",
      "why_relevant": ""
    }}
  ],
  "estimated_arv_from_comps": null,
  "confidence": 0.0,
  "notes": ""
}}
"""

    response = client.models.generate_content(
        model=GEMINI_COMPS_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    google_search=types.GoogleSearch()
                )
            ],
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )

    raw_text = response.text

    print("===== GEMINI COMPS RAW START =====")
    print(raw_text)
    print("===== GEMINI COMPS RAW END =====")

    try:
        return parse_json_safely(raw_text)
    except Exception as e:
        print("GEMINI COMPS JSON ERROR:", e)
        return {
            "comps_found": False,
            "comps": [],
            "estimated_arv_from_comps": None,
            "confidence": 0,
            "notes": f"Gemini comp search failed: {e}",
        }
def save_comps_to_db(listing, comps_data):
    PropertyComp.objects.filter(listing=listing).delete()

    comps = comps_data.get("comps", [])

    saved = []

    for comp in comps:
        saved_comp = PropertyComp.objects.create(
            listing=listing,
            address=str(comp.get("address") or ""),
            sold_price=clean_money(comp.get("sold_price")),
            beds=clean_decimal(comp.get("beds")),
            baths=clean_decimal(comp.get("baths")),
            sqft=clean_int(comp.get("sqft")),
            sold_date=str(comp.get("sold_date") or ""),
            distance_miles=clean_decimal(comp.get("distance_miles")),
            source_url=str(comp.get("source_url") or ""),
        )

        saved.append(saved_comp)

    return saved


def validate_arv_with_gemini_comps(listing):
    """
    Gets comps, saves them, and updates listing ARV only if Gemini returns a usable estimate.
    """

    comps_data = find_comps_with_gemini(listing)

    save_comps_to_db(listing, comps_data)

    estimated_arv = clean_money(comps_data.get("estimated_arv_from_comps"))

    if estimated_arv:
        listing.raw_llm_json = {
            **(listing.raw_llm_json or {}),
            "gemini_comps": comps_data,
            "original_email_arv": str(listing.arv) if listing.arv else None,
            "validated_arv": str(estimated_arv),
        }

        listing.arv = estimated_arv
        listing.save()

    return listing
