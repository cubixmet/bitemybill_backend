from django.db import models

from apps.common.models import RestaurantOwnedModel, TimeStampedModel


class LoyaltyProgram(RestaurantOwnedModel):
    name = models.CharField(max_length=80, default="BiteMyBills Rewards")
    points_per_amount = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    amount_step = models.DecimalField(max_digits=10, decimal_places=2, default=100)
    is_active = models.BooleanField(default=True)


class LoyaltyEarnRule(RestaurantOwnedModel):
    class Kind(models.TextChoices):
        ORDER_AMOUNT = "order_amount", "Order amount"
        MENU_ITEM = "menu_item", "Menu item"
        CATEGORY = "category", "Category"
        CAMPAIGN = "campaign", "Campaign multiplier"

    program = models.ForeignKey(LoyaltyProgram, on_delete=models.CASCADE, related_name="rules")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    label = models.CharField(max_length=120)
    points = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    multiplier = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    menu_item = models.ForeignKey("menu.MenuItem", null=True, blank=True, on_delete=models.CASCADE)
    category = models.ForeignKey("menu.MenuCategory", null=True, blank=True, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)


class Reward(RestaurantOwnedModel):
    class Kind(models.TextChoices):
        DISCOUNT = "discount", "Discount"
        FREE_ITEM = "free_item", "Free item"

    title = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    points_cost = models.DecimalField(max_digits=10, decimal_places=2)
    min_order = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    menu_item = models.ForeignKey("menu.MenuItem", null=True, blank=True, on_delete=models.SET_NULL)
    eligible_order_types = models.JSONField(default=list)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)


class LoyaltyLedger(RestaurantOwnedModel):
    class Kind(models.TextChoices):
        EARNED = "earned", "Earned"
        REDEEMED = "redeemed", "Redeemed"
        ADJUSTED = "adjusted", "Adjusted"
        EXPIRED = "expired", "Expired"

    customer = models.ForeignKey("customers.RestaurantCustomer", on_delete=models.CASCADE, related_name="point_ledger")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    points = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)


class RewardRedemption(RestaurantOwnedModel):
    customer = models.ForeignKey("customers.RestaurantCustomer", on_delete=models.CASCADE, related_name="redemptions")
    reward = models.ForeignKey(Reward, on_delete=models.PROTECT)
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL)
    points_spent = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, default="applied")
