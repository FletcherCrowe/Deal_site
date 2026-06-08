"""
views.py

Views are the functions connected to website pages.

Pages:
- Dashboard
- Settings
- Manual Deal Entry
- Gmail OAuth
- Read Gmail
- Email preview/detail
- Labeling
- Debug helpers
- WhatsApp test
"""

import os
import json
import traceback
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.clickjacking import xframe_options_exempt

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from .models import (
    Deal,
    EmailReadLog,
    GmailAccount,
    PropertyListing,
)

from .services import (
    get_settings,
    analyze_deal,
    read_gmail_deals,
    send_whatsapp_message,
    score_email_against_labeled_examples,
    get_gmail_service,
    get_allowed_zip_list,
    process_deal_after_llm_yes,
)


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def parse_decimal_setting(value, fallback):
    """
    Safely parse a decimal form value.
    """
    if value in [None, ""]:
        return fallback

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return fallback


def parse_int_setting(value, fallback):
    """
    Safely parse an integer form value.
    """
    if value in [None, ""]:
        return fallback

    try:
        return int(value)
    except ValueError:
        return fallback


def get_google_credentials_config():
    raw_creds = os.environ.get("GOOGLE_CREDENTIALS")

    if not raw_creds:
        raise Exception("GOOGLE_CREDENTIALS environment variable is missing.")

    return json.loads(raw_creds)


def get_redirect_uri():
    """
    Render OAuth callback URL.

    Make sure this exact URL is in Google Cloud Console:
    Authorized redirect URIs.
    """
    return "https://deal-site-s0jh.onrender.com/oauth2callback/"


# ------------------------------------------------------------
# Main pages
# ------------------------------------------------------------

def dashboard(request):
    """
    Dashboard now shows individual extracted property listings,
    not parent email rows.
    """

    listings = PropertyListing.objects.select_related("deal").order_by("-created_at")[:200]

    print("DASHBOARD LISTINGS COUNT:", listings.count())

    return render(request, "deals/dashboard.html", {
        "listings": listings,
    })


def settings_page(request):
    """
    Editable settings page.

    Controls:
    - Gmail query
    - WhatsApp API values
    - Buy box rules
    - ZIP codes
    - Formula values
    - LLM required categories
    - Offer template
    """

    settings = get_settings()
    gmail_account = GmailAccount.objects.first()

    if request.method == "POST":
        # Text fields
        settings.gmail_query = request.POST.get("gmail_query", settings.gmail_query)
        settings.whatsapp_token = request.POST.get("whatsapp_token", settings.whatsapp_token)
        settings.whatsapp_phone_number_id = request.POST.get(
            "whatsapp_phone_number_id",
            settings.whatsapp_phone_number_id
        )
        settings.whatsapp_to_number = request.POST.get(
            "whatsapp_to_number",
            settings.whatsapp_to_number
        )
        settings.allowed_zip_codes = request.POST.get(
            "allowed_zip_codes",
            settings.allowed_zip_codes
        )
        settings.llm_required_categories = request.POST.get(
            "llm_required_categories",
            settings.llm_required_categories
        )
        settings.offer_template = request.POST.get(
            "offer_template",
            settings.offer_template
        )

        # Buy box settings
        settings.min_beds = parse_decimal_setting(
            request.POST.get("min_beds"),
            settings.min_beds
        )
        settings.min_baths = parse_decimal_setting(
            request.POST.get("min_baths"),
            settings.min_baths
        )
        settings.max_price = parse_decimal_setting(
            request.POST.get("max_price"),
            settings.max_price
        )
        settings.min_year_built = parse_int_setting(
            request.POST.get("min_year_built"),
            settings.min_year_built
        )
        settings.min_sqft = parse_int_setting(
            request.POST.get("min_sqft"),
            settings.min_sqft
        )

        # Fix & Flip formula settings
        settings.flip_arv_multiplier = parse_decimal_setting(
            request.POST.get("flip_arv_multiplier"),
            settings.flip_arv_multiplier
        )
        settings.min_flip_profit = parse_decimal_setting(
            request.POST.get("min_flip_profit"),
            settings.min_flip_profit
        )

        # BRRRR formula settings
        settings.brrrr_loan_multiplier = parse_decimal_setting(
            request.POST.get("brrrr_loan_multiplier"),
            settings.brrrr_loan_multiplier
        )
        settings.max_brrrr_cash_left = parse_decimal_setting(
            request.POST.get("max_brrrr_cash_left"),
            settings.max_brrrr_cash_left
        )

        # Rehab fallback
        settings.rehab_cost_per_sqft = parse_decimal_setting(
            request.POST.get("rehab_cost_per_sqft"),
            settings.rehab_cost_per_sqft
        )

        settings.save()

        messages.success(request, "Settings saved successfully.")
        return redirect("settings")

    return render(request, "deals/settings.html", {
        "settings": settings,
        "gmail_account": gmail_account,
    })


def manual_deal(request):
    """
    Manual deal entry page.

    Useful for testing the calculation engine before Gmail parsing is perfect.
    """

    if request.method == "POST":
        deal = Deal.objects.create(
            address=request.POST.get("address", ""),
            zip_code=request.POST.get("zip_code", ""),
            price=request.POST.get("price") or None,
            beds=request.POST.get("beds") or None,
            baths=request.POST.get("baths") or None,
            sqft=request.POST.get("sqft") or None,
            year_built=request.POST.get("year_built") or None,
            arv=request.POST.get("arv") or None,
            rehab_cost=request.POST.get("rehab_cost") or None,
            rent=request.POST.get("rent") or None,
        )

        analyze_deal(deal)

        messages.success(request, "Manual deal analyzed.")
        return redirect("dashboard")

    return render(request, "deals/manual_deal.html")


# ------------------------------------------------------------
# Gmail OAuth
# ------------------------------------------------------------

def connect_gmail(request):
    google_creds = get_google_credentials_config()

    flow = Flow.from_client_config(
        google_creds,
        scopes=SCOPES,
        redirect_uri=get_redirect_uri(),
        autogenerate_code_verifier=True,
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes=False,
        prompt="consent",
    )

    request.session["state"] = state
    request.session["code_verifier"] = flow.code_verifier
    request.session.save()

    return redirect(authorization_url)


def oauth2callback(request):
    google_creds = get_google_credentials_config()

    state = request.session.get("state")
    code_verifier = request.session.get("code_verifier")

    flow = Flow.from_client_config(
        google_creds,
        scopes=SCOPES,
        state=state,
        redirect_uri=get_redirect_uri(),
        code_verifier=code_verifier,
    )

    flow.fetch_token(authorization_response=request.build_absolute_uri())

    creds = flow.credentials

    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()

    GmailAccount.objects.all().delete()

    GmailAccount.objects.create(
        email=profile.get("emailAddress", ""),
        token_json=json.loads(creds.to_json())
    )

    messages.success(request, f"Gmail connected as {profile.get('emailAddress', '')}.")
    return redirect("settings")


def gmail_status(request):
    account = GmailAccount.objects.first()

    if not account:
        return JsonResponse({
            "connected": False,
            "message": "No Gmail account connected.",
        })

    token = account.token_json

    return JsonResponse({
        "connected": True,
        "email": account.email,
        "connected_at": account.connected_at.isoformat(),
        "scopes": token.get("scopes") or token.get("scope"),
        "has_refresh_token": bool(token.get("refresh_token")),
        "token_uri": token.get("token_uri"),
        "client_id_ending": token.get("client_id", "")[-10:],
    })


def reset_gmail_connection(request):
    """
    Temporary helper.

    Remove before final production if you do not want anyone with the URL
    to be able to delete the Gmail connection.
    """
    GmailAccount.objects.all().delete()
    messages.success(request, "Gmail connection deleted. Reconnect Gmail now.")
    return redirect("settings")


def reset_all_email_data(request):
    """
    Temporary helper.

    Deletes all saved emails/listings and Gmail token.
    Remove before final production.
    """
    PropertyListing.objects.all().delete()
    Deal.objects.all().delete()
    GmailAccount.objects.all().delete()

    messages.success(request, "Deleted Gmail connection, saved emails, and listings.")
    return redirect("settings")


# ------------------------------------------------------------
# Gmail reading
# ------------------------------------------------------------

def read_gmail(request):
    """
    Button endpoint.

    Reads Gmail, parses matching emails, saves new deals/listings,
    and redirects back to the dashboard.
    """

    try:
        created = read_gmail_deals()
        messages.success(
            request,
            f"Gmail checked. Created {len(created)} new email/deal row(s)."
        )
    except Exception as e:
        messages.error(request, f"Gmail error: {e}")

    return redirect("dashboard")


def email_logs(request):
    logs = EmailReadLog.objects.order_by("-created_at")[:100]
    return render(request, "deals/email_logs.html", {"logs": logs})


def export_emails_view(request):
    gmail_account = GmailAccount.objects.first()
    deals = Deal.objects.order_by("-created_at")

    data = {
        "connected_gmail": gmail_account.email if gmail_account else None,
        "total_emails": deals.count(),
        "emails": []
    }

    for deal in deals:
        data["emails"].append({
            "id": deal.id,
            "email_id": deal.email_id,
            "sender": deal.sender,
            "subject": deal.subject,
            "body": deal.body,
            "raw_email_json": deal.raw_email_json,
            "parsed_data": {
                "address": deal.address,
                "zip_code": deal.zip_code,
                "price": str(deal.price) if deal.price is not None else None,
                "beds": str(deal.beds) if deal.beds is not None else None,
                "baths": str(deal.baths) if deal.baths is not None else None,
                "sqft": deal.sqft,
                "year_built": deal.year_built,
                "arv": str(deal.arv) if deal.arv is not None else None,
                "rehab_cost": str(deal.rehab_cost) if deal.rehab_cost is not None else None,
                "rent": str(deal.rent) if deal.rent is not None else None,
            },
            "created_at": deal.created_at.isoformat(),
        })

    return JsonResponse(data, safe=False)


# ------------------------------------------------------------
# Email detail / preview / labeling
# ------------------------------------------------------------

def email_detail(request, deal_id):
    deal = get_object_or_404(Deal, id=deal_id)

    return render(request, "deals/email_detail.html", {
        "deal": deal
    })


@xframe_options_exempt
def email_preview(request, deal_id):
    deal = get_object_or_404(Deal, id=deal_id)

    if deal.html_body:
        return HttpResponse(deal.html_body, content_type="text/html")

    text_html = f"""
    <html>
        <body style="font-family: Arial, sans-serif; white-space: pre-wrap; padding: 20px;">
            {deal.body}
        </body>
    </html>
    """

    return HttpResponse(text_html, content_type="text/html")


def save_email_label_from_detail(request, deal_id):
    deal = get_object_or_404(Deal, id=deal_id)

    if request.method != "POST":
        return redirect("email_detail", deal_id=deal.id)

    label = request.POST.get("label")
    label_notes = request.POST.get("label_notes", "")

    if label == "yes":
        deal.is_labeled = True
        deal.is_potential_lead = True
        deal.send_to_llm = True
        deal.label_notes = label_notes
        deal.save()
        messages.success(request, "Email labeled YES - Potential Lead.")

    elif label == "no":
        deal.is_labeled = True
        deal.is_potential_lead = False
        deal.send_to_llm = False
        deal.label_notes = label_notes
        deal.save()
        messages.success(request, "Email labeled NO - Not Useful.")

    else:
        messages.error(request, "Invalid label.")

    return redirect("email_detail", deal_id=deal.id)


def label_emails(request):
    deal = Deal.objects.filter(is_labeled=False).order_by("-created_at").first()

    if not deal:
        return render(request, "deals/label_done.html")

    return render(request, "deals/label_email.html", {"deal": deal})


def save_email_label(request, deal_id):
    deal = get_object_or_404(Deal, id=deal_id)

    if request.method != "POST":
        return redirect("label_emails")

    label = request.POST.get("label")

    if label == "yes":
        deal.is_labeled = True
        deal.is_potential_lead = True
        deal.send_to_llm = True
        deal.save()

    elif label == "no":
        deal.is_labeled = True
        deal.is_potential_lead = False
        deal.send_to_llm = False
        deal.save()

    else:
        messages.error(request, "Invalid label submitted.")

    return redirect("label_emails")


def export_labeled_emails(request):
    deals = Deal.objects.filter(is_labeled=True).order_by("-created_at")

    data = {
        "emails": []
    }

    for deal in deals:
        data["emails"].append({
            "email_id": deal.email_id,
            "sender": deal.sender,
            "subject": deal.subject,
            "body": deal.body,
            "is_potential_lead": deal.is_potential_lead,
            "send_to_llm": deal.send_to_llm,
            "difflib_score": deal.difflib_score,
            "label_notes": deal.label_notes,
            "created_at": deal.created_at.isoformat(),
        })

    return JsonResponse(data, safe=False)


def upload_labeled_emails(request):
    if request.method == "POST":
        uploaded_file = request.FILES.get("json_file")

        if not uploaded_file:
            messages.error(request, "No JSON file uploaded.")
            return redirect("upload_labeled_emails")

        try:
            data = json.load(uploaded_file)
            emails = data.get("emails", [])
            created_count = 0

            for item in emails:
                body = item.get("body", "")
                subject = item.get("subject", "")

                if not body:
                    continue

                existing = Deal.objects.filter(body=body, subject=subject).first()

                if existing:
                    continue

                deal = Deal.objects.create(
                    email_id=item.get("email_id") or None,
                    sender=item.get("sender", ""),
                    subject=subject,
                    body=body,
                    is_labeled=True,
                    is_potential_lead=bool(item.get("is_potential_lead", False)),
                    send_to_llm=bool(item.get("send_to_llm", False)),
                    label_notes=item.get("label_notes", ""),
                )

                score_email_against_labeled_examples(deal)
                created_count += 1

            for email in Deal.objects.filter(is_labeled=False):
                score_email_against_labeled_examples(email)

            messages.success(request, f"Imported {created_count} labeled emails.")

        except Exception as e:
            messages.error(request, f"Upload failed: {e}")

        return redirect("upload_labeled_emails")

    return render(request, "deals/upload_labeled_emails.html")


# ------------------------------------------------------------
# WhatsApp
# ------------------------------------------------------------

def test_whatsapp(request):
    status, text = send_whatsapp_message(
        "Test alert from your real estate deal dashboard."
    )

    messages.info(
        request,
        f"WhatsApp response: {status} - {text[:300]}"
    )

    return redirect("settings")


# ------------------------------------------------------------
# Debug helpers
# ------------------------------------------------------------

def debug_gmail_fetch(request):
    settings = get_settings()
    service = get_gmail_service()

    query = settings.gmail_query or "in:inbox newer_than:1d"

    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=10
    ).execute()

    messages_found = result.get("messages", [])

    output = {
        "query_used": query,
        "messages_found": len(messages_found),
        "messages": []
    }

    for msg in messages_found:
        full_msg = service.users().messages().get(
            userId="me",
            id=msg["id"],
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"]
        ).execute()

        headers = full_msg.get("payload", {}).get("headers", [])

        output["messages"].append({
            "id": msg["id"],
            "internalDate": full_msg.get("internalDate"),
            "headers": headers,
            "snippet": full_msg.get("snippet", "")
        })

    return JsonResponse(output, safe=False)


def debug_allowed_zips(request):
    return JsonResponse({
        "allowed_zips": get_allowed_zip_list(),
        "count": len(get_allowed_zip_list()),
    })


def debug_deal_settings(request):
    settings = get_settings()

    return JsonResponse({
        "allowed_zips": get_allowed_zip_list(),
        "min_beds": str(settings.min_beds),
        "min_baths": str(settings.min_baths),
        "max_price": str(settings.max_price),
        "min_year_built": settings.min_year_built,
        "min_sqft": settings.min_sqft,
        "flip_arv_multiplier": str(settings.flip_arv_multiplier),
        "min_flip_profit": str(settings.min_flip_profit),
        "brrrr_loan_multiplier": str(settings.brrrr_loan_multiplier),
        "max_brrrr_cash_left": str(settings.max_brrrr_cash_left),
        "rehab_cost_per_sqft": str(settings.rehab_cost_per_sqft),
    })


def debug_counts(request):
    return JsonResponse({
        "deals": Deal.objects.count(),
        "property_listings": PropertyListing.objects.count(),
        "latest_listings": [
            {
                "address": l.address,
                "zip_code": l.zip_code,
                "price": str(l.price),
                "qualifies": l.qualifies,
                "reason": l.reason,
                "email_subject": l.deal.subject,
            }
            for l in PropertyListing.objects.select_related("deal").order_by("-created_at")[:10]
        ]
    })


def debug_process_latest_deal(request):
    deal = Deal.objects.order_by("-created_at").first()

    if not deal:
        return JsonResponse({"error": "No deals found"})

    before = PropertyListing.objects.count()

    try:
        qualified = process_deal_after_llm_yes(deal)
    except Exception as e:
        return JsonResponse({
            "deal_id": deal.id,
            "subject": deal.subject,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "before_listings": before,
            "after_listings": PropertyListing.objects.count(),
        })

    return JsonResponse({
        "deal_id": deal.id,
        "subject": deal.subject,
        "before_listings": before,
        "after_listings": PropertyListing.objects.count(),
        "qualified_count": len(qualified),
        "latest_listings": [
            {
                "address": l.address,
                "zip_code": l.zip_code,
                "price": str(l.price),
                "qualifies": l.qualifies,
                "reason": l.reason,
            }
            for l in PropertyListing.objects.filter(deal=deal)
        ]
    })


def debug_process_deal(request, deal_id):
    deal = get_object_or_404(Deal, id=deal_id)

    before = PropertyListing.objects.count()

    try:
        qualified = process_deal_after_llm_yes(deal)
    except Exception as e:
        return JsonResponse({
            "deal_id": deal.id,
            "subject": deal.subject,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "before_listings": before,
            "after_listings": PropertyListing.objects.count(),
        })

    listings = PropertyListing.objects.filter(deal=deal)

    return JsonResponse({
        "deal_id": deal.id,
        "subject": deal.subject,
        "before_listings": before,
        "after_listings": PropertyListing.objects.count(),
        "listings_for_this_deal": listings.count(),
        "qualified_count": len(qualified),
        "latest_listings": [
            {
                "address": l.address,
                "zip_code": l.zip_code,
                "price": str(l.price),
                "arv": str(l.arv),
                "rehab_cost": str(l.rehab_cost),
                "beds": str(l.beds),
                "baths": str(l.baths),
                "sqft": l.sqft,
                "year_built": l.year_built,
                "qualifies": l.qualifies,
                "reason": l.reason,
            }
            for l in listings
        ]
    })