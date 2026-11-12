from django.conf import settings
from django.db import models

from apps.common.models import RestaurantOwnedModel


class Todo(RestaurantOwnedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.NORMAL)
    due_at = models.DateTimeField(null=True, blank=True)
    """When the task is due."""
    remind_at = models.DateTimeField(null=True, blank=True)
    """Local client alarm time — devices schedule OS notifications; no push provider."""
    alarm_enabled = models.BooleanField(default=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_todos",
    )

    class Meta:
        ordering = ["status", "due_at", "-created_at"]
        indexes = [
            models.Index(fields=["restaurant", "status"]),
            models.Index(fields=["restaurant", "remind_at"]),
        ]

    def __str__(self):
        return self.title
