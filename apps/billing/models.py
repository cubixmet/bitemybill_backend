from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class CatalogModule(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    group = models.CharField(max_length=40, blank=True)
    description = models.TextField(blank=True)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)
    aliases = models.JSONField(default=list)


class RestaurantModule(TimeStampedModel):
    restaurant = models.ForeignKey("restaurants.Restaurant", on_delete=models.CASCADE, related_name="enabled_modules")
    module = models.ForeignKey(CatalogModule, on_delete=models.PROTECT, related_name="restaurant_links")
    enabled = models.BooleanField(default=True)
    enabled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("restaurant", "module")


class Wallet(TimeStampedModel):
    restaurant = models.OneToOneField("restaurants.Restaurant", on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    next_debit_date = models.DateField(null=True, blank=True)


class WalletTransaction(TimeStampedModel):
    class Type(models.TextChoices):
        TOP_UP = "top_up", "Top-up"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Adjustment"
        USAGE_DEBIT = "usage_debit", "Usage debit"
        DUES_RECOVERY = "dues_recovery", "Dues recovery"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        PENDING = "pending", "Pending"

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    restaurant = models.ForeignKey("restaurants.Restaurant", on_delete=models.CASCADE, related_name="wallet_transactions")
    entry_type = models.CharField(max_length=24, choices=Type.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUCCESS)
    description = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=80, blank=True)
    razorpay_payment_id = models.CharField(max_length=64, blank=True, unique=True, null=True)


class UsageTransaction(TimeStampedModel):
    class Status(models.TextChoices):
        PAID = "paid", "Paid"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        UNPAID = "unpaid", "Unpaid"

    restaurant = models.ForeignKey("restaurants.Restaurant", on_delete=models.CASCADE, related_name="usage_transactions")
    period = models.CharField(max_length=40)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    debited = models.DecimalField(max_digits=12, decimal_places=2)
    outstanding = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PAID)
    wallet_transaction = models.ForeignKey(WalletTransaction, null=True, blank=True, on_delete=models.SET_NULL)


class UsageLine(TimeStampedModel):
    usage = models.ForeignKey(UsageTransaction, on_delete=models.CASCADE, related_name="lines")
    module = models.ForeignKey(CatalogModule, on_delete=models.PROTECT)
    label = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=12, decimal_places=2)


class RazorpayOrder(TimeStampedModel):
    restaurant = models.ForeignKey("restaurants.Restaurant", on_delete=models.CASCADE, related_name="razorpay_orders")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    razorpay_order_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, default="created")


class SetupRequest(TimeStampedModel):
    restaurant_name = models.CharField(max_length=160)
    owner_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    city = models.CharField(max_length=80, blank=True)
    message = models.TextField(blank=True)
    module_codes = models.JSONField(default=list)
    monthly_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    restaurant_type = models.CharField(max_length=80, blank=True)
    kind = models.CharField(max_length=20, default="setup")  # setup | contact
