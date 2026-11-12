from django.conf import settings
from django.db import models

from apps.common.models import RestaurantOwnedModel


class DeliveryAgent(RestaurantOwnedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="delivery_agents"
    )
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    vehicle = models.CharField(max_length=80, blank=True)
    is_available = models.BooleanField(default=True)


class DeliveryJob(RestaurantOwnedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ASSIGNED = "assigned", "Assigned"
        PICKED_UP = "picked_up", "Picked up"
        ON_THE_WAY = "on_the_way", "On the way"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="delivery_job")
    agent = models.ForeignKey(DeliveryAgent, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    address = models.TextField(blank=True)
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True)
