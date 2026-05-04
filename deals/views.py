"""
views.py

Views are the functions connected to website pages.

Each view receives a web request and returns a web response.

Pages in this milestone:
- Dashboard
- Settings
- Manual Deal Entry
- Read Gmail
- Test WhatsApp
"""

from django.contrib import messages
from django.shortcuts import render, redirect

from .models import Deal,EmailReadLog
from .services import (
    get_settings,
    analyze_deal,
    read_gmail_deals,
    send_whatsapp_message,
)
import os
import json
from django.shortcuts import redirect
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from .models import GmailAccount


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

def get_google_credentials_config():
    raw_creds = os.environ.get("GOOGLE_CREDENTIALS")

    if not raw_creds:
        raise Exception("GOOGLE_CREDENTIALS environment variable is missing.")

    return json.loads(raw_creds)


def get_redirect_uri():
    return "https://deal-site-s0jh.onrender.com"


def connect_gmail(request):
    google_creds = get_google_credentials_config()

    flow = Flow.from_client_config(
        google_creds,
        scopes=SCOPES,
        redirect_uri=get_redirect_uri()
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    request.session["state"] = state

    return redirect(authorization_url)

def oauth2callback(request):
    google_creds = get_google_credentials_config()
    state = request.session.get("state")

    flow = Flow.from_client_config(
        google_creds,
        scopes=SCOPES,
        state=state,
        redirect_uri=get_redirect_uri()
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

    return redirect("settings")
def email_logs(request):
    logs = EmailReadLog.objects.order_by("-created_at")[:100]
    return render(request, "deals/email_logs.html", {"logs": logs})
def dashboard(request):
    """
    Main homepage.

    Shows recent deals and their calculated results.
    """

    deals = Deal.objects.order_by("-created_at")[:20]

    return render(
        request,
        "deals/dashboard.html",
        {
            "deals": deals
        }
    )


def settings_page(request):
    """
    Editable settings page.

    This lets the user change:
    - Gmail query
    - WhatsApp API values
    - buy box rules
    - formula values
    - offer template

    For now, this is a simple manual HTML form.
    Later, you can replace this with Django forms.
    """

    settings = get_settings()

    if request.method == "POST":
        # Text fields.
        text_fields = [
            "gmail_query",
            "whatsapp_token",
            "whatsapp_phone_number_id",
            "whatsapp_to_number",
            "allowed_zip_codes",
            "offer_template",
        ]

        for field in text_fields:
            setattr(settings, field, request.POST.get(field, ""))

        # Numeric fields.
        number_fields = [
            "min_beds",
            "min_baths",
            "max_price",
            "min_year_built",
            "min_sqft",
            "flip_arv_multiplier",
            "brrrr_loan_multiplier",
            "min_flip_profit",
            "max_brrrr_cash_left",
            "rehab_cost_per_sqft",
        ]

        for field in number_fields:
            value = request.POST.get(field)

            if value not in [None, ""]:
                setattr(settings, field, value)

        settings.save()

        messages.success(request, "Settings saved successfully.")
        return redirect("settings")

    return render(
        request,
        "deals/settings.html",
        {
            "settings": settings
        }
    )


def manual_deal(request):
    """
    Manual deal entry page.

    This is useful for the first milestone because you can test the
    calculation engine before Gmail parsing is perfect.
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


def read_gmail(request):
    """
    Button endpoint.

    When visited, it reads Gmail, parses matching emails, saves new deals,
    and redirects back to the dashboard.

    Later this should become a background task.
    For Milestone 1, a button is easier to debug.
    """

    try:
        created = read_gmail_deals()
        messages.success(
            request,
            f"Gmail checked. Created {len(created)} new deal(s)."
        )
    except Exception as e:
        messages.error(request, f"Gmail error: {e}")

    return redirect("dashboard")


def test_whatsapp(request):
    """
    Sends a test WhatsApp alert.

    This confirms that the WhatsApp Cloud API settings are correct.
    """

    status, text = send_whatsapp_message(
        "Test alert from your real estate deal dashboard."
    )

    messages.info(
        request,
        f"WhatsApp response: {status} - {text[:300]}"
    )

    return redirect("settings")