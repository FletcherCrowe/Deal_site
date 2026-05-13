import json
from django.core.management.base import BaseCommand
from deals.models import Deal


class Command(BaseCommand):
    help = "Export saved emails to JSON for labeling."

    def handle(self, *args, **kwargs):
        deals = Deal.objects.order_by("-created_at")

        data = []

        for deal in deals:
            data.append({
                "id": deal.id,
                "email_id": deal.email_id,
                "sender": deal.sender,
                "subject": deal.subject,
                "body": deal.body,
                "price": deal.price,
                "beds": deal.beds,
                "baths": deal.baths,
                "sqft": deal.sqft,
                "year_built": deal.year_built,
                "arv": deal.arv,
                "rehab_cost": deal.rehab_cost,
                "rent": deal.rent,
                "qualifies": deal.qualifies,
                "recommendation": deal.recommendation,
                "created_at": deal.created_at.isoformat(),
            })

        with open("email_export.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Exported {len(data)} emails."))