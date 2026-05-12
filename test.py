from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os
import pickle
from whatsapp_api_client_python import API
import base64
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send"
]

def generate_token():

    flow = InstalledAppFlow.from_client_secrets_file(
        r"C:\Users\Fletcher\Downloads\client_secret_618687093416-saftgn8idas2gi3ph0dipmg4pd00m9q5.apps.googleusercontent.com.json",
        SCOPES
    )

    creds = flow.run_local_server(port=0)

    with open("token.json", "w") as token:
        token.write(creds.to_json())

    print("token.json created!")


def get_email(email_number):
    """
    Get a specific Gmail by its position.

    Example:
        get_email(50)
        -> returns the 50th newest email
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    creds = Credentials.from_authorized_user_file(
        r"C:\Users\Fletcher\Desktop\Projects\deal_site\token.json",
        SCOPES
    )

    service = build("gmail", "v1", credentials=creds)

    # Get enough emails to reach the one requested
    results = service.users().messages().list(
        userId="me",
        q="from:gmusb@gmail.com"
    ).execute()

    messages = results.get("messages", [])

    if len(messages) < email_number:
        return "Not enough emails found."

    # Get the specific email
    target_email = messages[email_number - 1]

    msg_data = service.users().messages().get(
        userId="me",
        id=target_email["id"]
    ).execute()

    headers = msg_data["payload"]["headers"]

    subject = ""
    sender = ""

    for header in headers:
        if header["name"] == "Subject":
            subject = header["value"]

        if header["name"] == "From":
            sender = header["value"]

    return {
        "id": target_email["id"],
        "subject": subject,
        "from": sender,
        "snippet": msg_data.get("snippet", "")
    }

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import base64

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
    results = service.users().messages().list(
        userId="me",
        q=f"from:{sender_email}",
        maxResults=email_number
    ).execute()

    messages = results.get("messages", [])

    if len(messages) < email_number:
        return "Not enough emails found."

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


# Example:

# Example:

def send_whatsapp_message(message):

    greenAPI = API.GreenAPI(
        '7107614249',
        '883f8c8d0288422c94701556bf62e9bf1e9873053788425092'
    )

    chat_id = f"{17065841875}@c.us"

    response = greenAPI.sending.sendMessage(
        chat_id,
        message
    )

    return response.data
#print(emails['body'])


def get_10_emails():

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    creds = Credentials.from_authorized_user_file(
        "token.json",
        SCOPES
    )

    service = build("gmail", "v1", credentials=creds)

    results = service.users().messages().list(
        userId="me",
        maxResults=10
    ).execute()

    messages = results.get("messages", [])

    email_list = []

    for msg in messages:

        message = service.users().messages().get(
            userId="me",
            id=msg["id"]
        ).execute()

        headers = message["payload"]["headers"]

        subject = ""
        sender = ""

        for header in headers:

            if header["name"] == "Subject":
                subject = header["value"]

            elif header["name"] == "From":
                sender = header["value"]

        email_list.append({
            "id": msg["id"],
            "from": sender,
            "subject": subject,
            "snippet": message.get("snippet", "")
        })

    return email_list


# Example
generate_token()
emails = get_10_emails()

for email in emails:
    print(email)
    print("=" * 50)
#send_whatsapp_message(emails['body'])
