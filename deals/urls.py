from django.urls import path
from . import views


urlpatterns = [
    # Dashboard/homepage.
    path("", views.dashboard, name="dashboard"),

    # Settings page.
    path("settings/", views.settings_page, name="settings"),

    # Manual deal entry.
    path("manual-deal/", views.manual_deal, name="manual_deal"),

    # Gmail reader button.
    path("read-gmail/", views.read_gmail, name="read_gmail"),

    # WhatsApp test button.
    path("test-whatsapp/", views.test_whatsapp, name="test_whatsapp"),
        #Email logs
    path("email-logs/", views.email_logs, name="email_logs"),
    path("connect-gmail/", views.connect_gmail, name="connect_gmail"),
    path("oauth2callback/", views.oauth2callback, name="oauth2callback"),
    path("export-emails/", views.export_emails_view),
   path("label-emails/", views.label_emails, name="label_emails"),
    path("label-emails/<int:deal_id>/save/", views.save_email_label, name="save_email_label"),
    path("email-preview/<int:deal_id>/", views.email_preview, name="email_preview"),
    path("export-labeled-emails/", views.export_labeled_emails, name="export_labeled_emails"),
    path("upload-labeled-emails/", views.upload_labeled_emails, name="upload_labeled_emails"),
    path("reset-gmail/", views.reset_gmail_connection, name="reset_gmail_connection"),
    path("gmail-status/", views.gmail_status, name="gmail_status"),
    path("email-detail/<int:deal_id>/", views.email_detail, name="email_detail"),
    path(
        "email-detail/<int:deal_id>/save-label/",
        views.save_email_label_from_detail,
        name="save_email_label_from_detail"
    ),
    path(
    "reset-all-email-data/",
    views.reset_all_email_data,
    name="reset_all_email_data"
    ),
    path("debug-gmail-fetch/", views.debug_gmail_fetch, name="debug_gmail_fetch"),
]