import hashlib
import secrets

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel
from apps.restaurants.constants import PERMISSION_ACTIONS, PERMISSION_SCOPES
from apps.restaurants.storefront_hosts import storefront_host, storefront_subdomain, storefront_url


class Restaurant(TimeStampedModel):
    class AccountStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        PAYMENT_DUE = "payment_due", "Payment Due"
        SCHEDULED_FOR_DELETION = "scheduled_for_deletion", "Scheduled for deletion"

    class CustomDomainStatus(models.TextChoices):
        NONE = "none", "None"
        PENDING = "pending", "Pending DNS"
        ACTIVE = "active", "Active"

    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True, max_length=80)
    owner_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    restaurant_type = models.CharField(max_length=80, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    branch = models.CharField(max_length=80, default="Main")
    location = models.CharField(max_length=160, blank=True)
    initials = models.CharField(max_length=4, blank=True)
    currency = models.CharField(max_length=8, default="INR")
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    order_prefix = models.CharField(max_length=8, default="MK")
    theme = models.CharField(max_length=40, default="classic-blue")
    description = models.TextField(blank=True)
    # Custom branded site (manually designed) — requires `custom_storefront` module.
    custom_domain = models.CharField(max_length=255, blank=True, db_index=True)
    custom_domain_status = models.CharField(
        max_length=20, choices=CustomDomainStatus.choices, default=CustomDomainStatus.NONE
    )
    account_status = models.CharField(
        max_length=32, choices=AccountStatus.choices, default=AccountStatus.ACTIVE
    )
    grace_months_used = models.PositiveSmallIntegerField(default=0)
    outstanding_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_operational(self):
        return self.account_status != self.AccountStatus.SCHEDULED_FOR_DELETION and not self.is_archived

    @property
    def pwa_subdomain(self) -> str:
        return storefront_subdomain(self.slug)

    @property
    def pwa_host(self) -> str:
        return storefront_host(self.slug)

    @property
    def pwa_url(self) -> str:
        return storefront_url(self.slug)


class RestaurantSettings(TimeStampedModel):
    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE, related_name="settings")
    tax_name = models.CharField(max_length=20, default="GST")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=5)
    tax_inclusive = models.BooleanField(default=False)
    order_types = models.JSONField(default=list)
    payment_methods = models.JSONField(default=list)
    opening_time = models.CharField(max_length=8, default="11:00")
    closing_time = models.CharField(max_length=8, default="23:30")
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=40)
    packaging_fee = models.DecimalField(max_digits=10, decimal_places=2, default=15)
    min_order = models.DecimalField(max_digits=10, decimal_places=2, default=300)
    delivery_radius_km = models.DecimalField(max_digits=6, decimal_places=2, default=6)
    invoice_footer = models.TextField(blank=True)
    # Location-based login / on-site check-in
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    login_radius_m = models.PositiveIntegerField(default=200)
    require_location_login = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.order_types:
            self.order_types = ["Dine-in", "Takeaway", "Delivery"]
        if not self.payment_methods:
            self.payment_methods = ["Cash", "UPI", "Card"]
        super().save(*args, **kwargs)


class RestaurantTheme(TimeStampedModel):
    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE, related_name="theme_record")
    theme_id = models.CharField(max_length=40, default="classic-blue")
    logo_name = models.CharField(max_length=160, blank=True)
    cover_name = models.CharField(max_length=160, blank=True)


class Permission(TimeStampedModel):
    scope = models.CharField(max_length=40, choices=[(s, s) for s in PERMISSION_SCOPES])
    action = models.CharField(max_length=20, choices=[(a, a) for a in PERMISSION_ACTIONS])

    class Meta:
        unique_together = ("scope", "action")

    def __str__(self):
        return f"{self.scope}:{self.action}"


class Role(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    is_system = models.BooleanField(default=True)
    permissions = models.ManyToManyField(Permission, blank=True, related_name="roles")

    def __str__(self):
        return self.name


class RestaurantMembership(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="memberships")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="memberships")
    is_active = models.BooleanField(default=True)
    pos_pin = models.CharField(max_length=128, blank=True)

    class Meta:
        unique_together = ("user", "restaurant")

    def has_permission(self, scope: str, action: str = "View") -> bool:
        if self.role.code == "owner":
            return True
        return self.role.permissions.filter(scope=scope, action=action).exists()


class StorefrontAPIToken(TimeStampedModel):
    """Long-lived token for custom restaurant websites to call the partner data API."""

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="storefront_api_tokens")
    name = models.CharField(max_length=120, default="Custom website")
    prefix = models.CharField(max_length=16, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)  # e.g. ["menu", "orders", "loyalty", "customers"]
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_storefront_tokens"
    )

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def hash_token(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, *, restaurant, name: str, scopes=None, created_by=None):
        raw = f"bmb_sf_{secrets.token_urlsafe(32)}"
        obj = cls.objects.create(
            restaurant=restaurant,
            name=name or "Custom website",
            prefix=raw[:16],
            token_hash=cls.hash_token(raw),
            scopes=scopes or ["menu", "orders", "loyalty", "restaurant"],
            created_by=created_by,
        )
        return obj, raw

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
