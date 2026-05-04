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
]