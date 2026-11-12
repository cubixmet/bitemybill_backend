from django.conf import settings
from django.db import models

from apps.common.models import RestaurantOwnedModel, TimeStampedModel


class RestaurantCustomer(RestaurantOwnedModel):
    class Source(models.TextChoices):
        ENQUIRY = "enquiry", "Enquiry"
        WALK_IN = "walk_in", "Walk-in"
        POS = "pos", "POS"
        APP = "app", "Customer app"
        ONLINE = "online", "Online ordering"
        DELIVERY = "delivery", "Delivery"
        IMPORT = "import", "Import"
        OTHER = "other", "Other"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="customer_profiles"
    )
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, db_index=True)
    email = models.EmailField(blank=True)
    status = models.CharField(max_length=20, default="regular")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.WALK_IN, db_index=True)
    enquiry_note = models.CharField(max_length=255, blank=True)
    enquiry_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    points_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("restaurant", "phone")

    @property
    def is_app_user(self) -> bool:
        return bool(self.user_id) or self.source == self.Source.APP


class CustomerAddress(TimeStampedModel):
    customer = models.ForeignKey(RestaurantCustomer, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=40, default="Home")
    line = models.CharField(max_length=255)
    landmark = models.CharField(max_length=120, blank=True)
    is_default = models.BooleanField(default=False)


class FavoriteItem(TimeStampedModel):
    customer = models.ForeignKey(RestaurantCustomer, on_delete=models.CASCADE, related_name="favorites")
    menu_item = models.ForeignKey("menu.MenuItem", on_delete=models.CASCADE)
