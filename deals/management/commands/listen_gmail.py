import time
from django.core.management.base import BaseCommand
from deals.services import read_gmail_deals


class Command(BaseCommand):
    help = "Continuously checks Gmail and saves new emails."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seconds",
            type=int,
            default=60,
            help="How often to check Gmail."
        )

    def handle(self, *args, **options):
        seconds = options["seconds"]

        self.stdout.write(self.style.SUCCESS("Starting Gmail listener..."))

        while True:
            try:
                created = read_gmail_deals()

                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f"Saved {len(created)} new email(s).")
                    )
                else:
                    self.stdout.write("No new emails.")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Gmail listener error: {e}"))

            time.sleep(seconds)