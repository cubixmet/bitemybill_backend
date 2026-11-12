from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Notification(TimeStampedModel):
    restaurant = models.ForeignKey(
        "restaurants.Restaurant", null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    kind = models.CharField(max_length=40, default="info")
    is_read = models.BooleanField(default=False)
