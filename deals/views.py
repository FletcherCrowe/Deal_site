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

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from .models import Deal
from .services import score_email_against_labeled_examples
from .models import GmailAccount
import os
import json
from googleapiclient.discovery import build

from django.http import JsonResponse
from django.http import HttpResponse
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
                "price": deal.price,
                "beds": deal.beds,
                "baths": deal.baths,
                "sqft": deal.sqft,
                "year_built": deal.year_built,
                "arv": deal.arv,
                "rehab_cost": deal.rehab_cost,
                "rent": deal.rent,
            },
            "created_at": deal.created_at.isoformat(),
        })

    return JsonResponse(data, safe=False)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


def get_google_credentials_config():
    raw_creds = os.environ.get("GOOGLE_CREDENTIALS")

    if not raw_creds:
        raise Exception("GOOGLE_CREDENTIALS environment variable is missing.")

    return json.loads(raw_creds)


def get_redirect_uri():
    return "https://deal-site-s0jh.onrender.com/oauth2callback/"


def connect_gmail(request):
    google_creds = get_google_credentials_config()

    flow = Flow.from_client_config(
        google_creds,
        scopes=SCOPES,
        redirect_uri=get_redirect_uri(),
        autogenerate_code_verifier=True,  # 🔥 IMPORTANT FIX
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        prompt="consent",
    )

    request.session["state"] = state
    request.session["code_verifier"] = flow.code_verifier  # 🔥 SAVE THIS
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
        code_verifier=code_verifier,  # 🔥 IMPORTANT FIX
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
    gmail_account = GmailAccount.objects.first()
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
        return render(
        request,
        "deals/settings.html",
        {
            "settings": settings,
            "gmail_account": gmail_account,
        }
        )
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
    """
    Exports all labeled emails as JSON.

    You can save this file and use it as your training/knowledge base.
    """

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
    """
    Lets you upload a JSON file of labeled emails.

    Expected format:

    {
      "emails": [
        {
          "subject": "Example",
          "body": "Email text...",
          "is_potential_lead": true,
          "send_to_llm": true,
          "label_notes": "optional notes"
        }
      ]
    }
    """

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

                # Prevent exact duplicate imports.
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

            # Re-score all unlabeled emails after adding new examples.
            for email in Deal.objects.filter(is_labeled=False):
                score_email_against_labeled_examples(email)

            messages.success(request, f"Imported {created_count} labeled emails.")

        except Exception as e:
            messages.error(request, f"Upload failed: {e}")

        return redirect("upload_labeled_emails")

    return render(request, "deals/upload_labeled_emails.html")

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