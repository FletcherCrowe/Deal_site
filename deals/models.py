from django.db import models

raw_email_json = models.JSONField(blank=True, null=True)
processed_for_training = models.BooleanField(default=False)
class PropertyListing(models.Model):
    deal = models.ForeignKey("Deal", on_delete=models.CASCADE, related_name="listings")

    address = models.CharField(max_length=500, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)

    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    arv = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rehab_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rent = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    taxes = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    beds = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    baths = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    sqft = models.IntegerField(null=True, blank=True)
    year_built = models.IntegerField(null=True, blank=True)
    suggested_offer = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    zip_allowed = models.BooleanField(default=False)

    mao = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    flip_profit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    brrrr_cash_left = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    qualifies_flip = models.BooleanField(default=False)
    qualifies_brrrr = models.BooleanField(default=False)
    qualifies = models.BooleanField(default=False)

    reason = models.TextField(blank=True)
    raw_llm_json = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.address or f"Listing {self.id}"
class AppSettings(models.Model):
    """
    This model stores the editable settings for the entire app.

    The goal is to avoid hardcoding business rules.

    Instead of changing Python code every time the client changes:
    - zip codes
    - buy box rules
    - formula multipliers
    - WhatsApp number
    - Gmail search query
    - offer template

    they can edit those values from the website.
    """

    # Gmail search query.
    # This controls which emails the system looks for.
    # Example Gmail searches:
    # subject:(deal OR property OR wholesale)
    # from:example@email.com
    # newer_than:7d
    gmail_query = models.CharField(
        max_length=255,
        default='subject:(deal OR property OR wholesale)'
    )
    llm_required_categories = models.TextField(
    default="""Location/address/city/zip
    Price/list price/asking price
    Year built
    Square footage
    ARV / after repair value
    Beds and baths
    Taxes
    Rehab estimate
    Rent estimate
    Suggested offer"""
    )
    # WhatsApp Cloud API settings.
    # This is for Meta's official WhatsApp API, not Twilio.
    whatsapp_token = models.TextField(blank=True)
    whatsapp_phone_number_id = models.CharField(max_length=255, blank=True)
    whatsapp_to_number = models.CharField(max_length=50, blank=True)

    # Buy box rules.
    min_beds = models.FloatField(default=2)
    min_baths = models.FloatField(default=1)
    max_price = models.IntegerField(default=150000)
    min_year_built = models.IntegerField(default=1970)
    min_sqft = models.IntegerField(default=1300)

    # Stored as comma-separated zip codes.
    # Example: 75201,75204,75206
    allowed_zip_codes = models.TextField(default="", blank=True)

    # Formula settings.
    # These are editable so the client can adjust their investment rules.
    flip_arv_multiplier = models.FloatField(default=0.70)
    brrrr_loan_multiplier = models.FloatField(default=0.75)
    min_flip_profit = models.IntegerField(default=30000)
    max_brrrr_cash_left = models.IntegerField(default=5000)

    # If the email does not include rehab cost,
    # the app calculates rehab as:
    # sqft * rehab_cost_per_sqft
    rehab_cost_per_sqft = models.IntegerField(default=35)
    difflib_confidence_threshold = models.FloatField(default=0.60)
    # Future setting.
    # Not fully used yet, but useful when you add auto-reply.
    offer_template = models.TextField(default="""Hi,

Thank you for sending this deal.

Based on our numbers, we would be interested at {{ mao }}.

Thanks""")


class Deal(models.Model):
    """
    This model stores each property/deal found by the system.

    A deal can come from:
    - manual entry through the website
    - Gmail parsing

    Later, this can also store:
    - comparable properties
    - Google Sheets sync status
    - offer email status
    - WhatsApp alert status
    """
    raw_email_json = models.JSONField(blank=True, null=True)
    custom_identifier = models.CharField(max_length=255, blank=True)
    important_notes = models.TextField(blank=True)
    # Gmail message ID.
    # This prevents processing the same email more than once.
    email_id = models.CharField(
        max_length=255,
        blank=True,
        unique=True,
        null=True
    )
    zip_allowed = models.BooleanField(default=False)
    math_checked = models.BooleanField(default=False)
    math_qualifies = models.BooleanField(default=False)
    math_reason = models.TextField(blank=True)
    gmail_received_at = models.DateTimeField(null=True, blank=True)
    llm_checked = models.BooleanField(default=False)
    llm_is_valid_lead = models.BooleanField(default=False)
    llm_reason = models.TextField(blank=True)
    llm_missing_fields = models.JSONField(blank=True, null=True)
    # Email source info.
    sender = models.EmailField(blank=True)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    html_body = models.TextField(blank=True)
    # Property info.
    address = models.CharField(max_length=255, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)

    price = models.IntegerField(null=True, blank=True)
    beds = models.FloatField(null=True, blank=True)
    baths = models.FloatField(null=True, blank=True)
    sqft = models.IntegerField(null=True, blank=True)
    year_built = models.IntegerField(null=True, blank=True)
    arv = models.IntegerField(null=True, blank=True)
    rehab_cost = models.IntegerField(null=True, blank=True)
    rent = models.IntegerField(null=True, blank=True)

    # Calculated results.
    mao = models.IntegerField(null=True, blank=True)
    flip_profit = models.IntegerField(null=True, blank=True)
    brrrr_cash_left = models.IntegerField(null=True, blank=True)

    # Final decision.
    qualifies = models.BooleanField(default=False)
    recommendation = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    # Labeling fields
    is_labeled = models.BooleanField(default=False)
    is_potential_lead = models.BooleanField(default=False)
    send_to_llm = models.BooleanField(default=False)

    # Difflib scoring fields
    difflib_score = models.FloatField(null=True, blank=True)
    matched_example_subject = models.CharField(max_length=255, blank=True)
    matched_example_body = models.TextField(blank=True)

    # Optional notes
    label_notes = models.TextField(blank=True)
    def __str__(self):
        return self.address or self.subject or f"Deal {self.id}"
class EmailReadLog(models.Model):
    email_id = models.CharField(max_length=255, blank=True)
    sender = models.EmailField(blank=True)
    subject = models.CharField(max_length=255, blank=True)
    snippet = models.TextField(blank=True)

    status = models.CharField(max_length=50, default="found")
    error_message = models.TextField(blank=True)

    deal_created = models.BooleanField(default=False)
    qualifies = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.status} - {self.subject}"
class GmailAccount(models.Model):
    email = models.EmailField(blank=True)
    token_json = models.JSONField()
    connected_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email or "Connected Gmail"
# client Id 977814781539-sqnh5qo1t0cms2cnggt2nsmu8lgudie3.apps.googleusercontent.com
class PropertyComp(models.Model):
    listing = models.ForeignKey(
        "PropertyListing",
        on_delete=models.CASCADE,
        related_name="comps"
    )

    address = models.CharField(max_length=500, blank=True)
    sold_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    beds = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    baths = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    sqft = models.IntegerField(null=True, blank=True)
    sold_date = models.CharField(max_length=100, blank=True)
    distance_miles = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    source_url = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)