from django.core.management.base import BaseCommand
from email.utils import parseaddr
import json
import os
from deals.models import Deal
from difflib import get_close_matches
def load_knowledge_base(file_path:str) -> dict:
    with open(file_path,'r') as file:
        data:dict=json.load(file)
    return data
def save_knowledge_base(file_path:str,data:dict):
    with open(file_path,'w') as file:
        json.dump(data,file,indent=2)
def find_best_match(user_question:str,questions:list[str]) -> str|None:
    matches:list=get_close_matches(user_question,questions,n=1,cutoff=0.6)
    return matches[0] if matches else None
def get_answer_for_question(question:str,knowledge_base:dict) -> str|None:
    for q in knowledge_base["questions"]:
        if q["question"]==question:
            return q["answer"]
def main_funct(email,actions,new_answer):
    knowledge_base:dict=load_knowledge_base(r'C:\Users\Fletcher\Desktop\Projects\SurveyAutomationUltamate\backgroundInfo.json')
    if True:
        user_input = email
        best_match:str|None=find_best_match(user_input,[q['question'] for q in knowledge_base['questions']])
        if best_match:
            answer:str=get_answer_for_question(best_match,knowledge_base)
            actions(action)
            print(f"Bot: {answer}")
        else:
            print("Bot: what is the page")
            action=new_answer.split(" ")[1]
            print(new_answer)
            if new_answer.lower()!='skip':
                knowledge_base["questions"].append({"question":user_input,"answer":new_answer,"action":action})
                save_knowledge_base('data.json',knowledge_base)
                print("Bot: Thankyou! I learnt a new message type!")
from deals.services import (
    get_settings,
    get_gmail_service,
    extract_message_body,
    parse_deal_from_text,
    analyze_deal,
)
json_file = "email_training_data.json"

# Load existing data if file exists
if os.path.exists(json_file):
    with open(json_file, "r", encoding="utf-8") as f:
        try:
            all_data = json.load(f)
        except:
            all_data = []
else:
    all_data = []

class Command(BaseCommand):
    help = "Review Gmail messages in terminal and manually label important parts."

    def handle(self, *args, **kwargs):
        settings = get_settings()
        service = get_gmail_service()

        self.stdout.write("\n===== GMAIL EMAIL REVIEW MODE =====")
        self.stdout.write(f"Using Gmail query: {settings.gmail_query}\n")

        result = service.users().messages().list(
            userId="me",
            maxResults=20
        ).execute()

        messages = result.get("messages", [])

        self.stdout.write(f"Found {len(messages)} emails.\n")

        for index, msg in enumerate(messages, start=1):
            email_id = msg["id"]

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

            body = extract_message_body(payload)

            print("\n" + "=" * 80)
            print(f"EMAIL {index}/{len(messages)}")
            print("=" * 80)
            print(f"Email ID: {email_id}")
            print(f"From: {sender}")
            print(f"Subject: {subject}")
            print("-" * 80)
            print("BODY:")
            print(body[:3000])
            print("-" * 80)

            parsed = parse_deal_from_text(body)

            print("CURRENT AUTO-PARSED DATA:")
            print(parsed)
            print("-" * 80)

            choice = input("Save this email as a deal? (y/n/skip/quit): ").strip().lower()

            if choice == "quit":
                print("Exiting review mode.")
                break
            if choice == "y":
                entry = {
                "email_id": email_id,
                "sender": sender,
                "subject": subject,
                "body": body,
                "parsed": parsed,
                "choice":choice
                }

                all_data.append(entry)

                # Save to file immediately (so you don't lose progress)
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(all_data, f, indent=2)

            print("Saved to JSON file.")
            if choice in ["n", "skip", ""]:
                entry = {
                "email_id": email_id,
                "sender": sender,
                "subject": subject,
                "body": body,
                "parsed": parsed,
                "choice":choice
                }

                all_data.append(entry)

                # Save to file immediately (so you don't lose progress)
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(all_data, f, indent=2)

                print("Skipped.")
                continue

            custom_identifier = input(
                "Custom identifier/type for this email "
                "(example: wholesale_deal, realtor_alert, bad_format): "
            ).strip()

            important_notes = input(
                "What part is important? "
                "(example: price line, ARV section, address block): "
            ).strip()

            if Deal.objects.filter(email_id=email_id).exists():
                deal = Deal.objects.get(email_id=email_id)
                print("Existing deal found. Updating it.")
            else:
                deal = Deal.objects.create(
                    email_id=email_id,
                    sender=sender,
                    subject=subject,
                    body=body,
                    **parsed
                )

            deal.custom_identifier = custom_identifier
            deal.important_notes = important_notes
            deal.save()

            analyze_deal(deal)

            print("\nSAVED DEAL")
            print(f"Identifier: {deal.custom_identifier}")
            print(f"Notes: {deal.important_notes}")
            print(f"Qualifies: {deal.qualifies}")
            print(f"Recommendation: {deal.recommendation}")
#max