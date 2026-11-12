from django.core.exceptions import ValidationError
from django.db import models

from apps.common.models import RestaurantOwnedModel


class ExpenseCategory(RestaurantOwnedModel):
    """
    Two-level expense taxonomy.
    Parent (level 1): Rent, Tax, Event, Utilities…
    Child (level 2): Shop rent, GST, Birthday catering… under a parent.
    """

    name = models.CharField(max_length=80)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        unique_together = ("restaurant", "parent", "name")

    def __str__(self):
        if self.parent_id:
            return f"{self.parent.name} / {self.name}"
        return self.name

    def clean(self):
        if self.parent_id:
            if self.parent.restaurant_id != self.restaurant_id:
                raise ValidationError({"parent": "Parent must belong to the same restaurant"})
            if self.parent.parent_id:
                raise ValidationError({"parent": "Only two category levels are allowed"})
            if self.pk and self.parent_id == self.pk:
                raise ValidationError({"parent": "Category cannot be its own parent"})

    @property
    def is_parent(self) -> bool:
        return self.parent_id is None

    @property
    def full_path(self) -> str:
        if self.parent_id:
            return f"{self.parent.name} / {self.name}"
        return self.name


class Expense(RestaurantOwnedModel):
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    payee = models.CharField(max_length=160)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    method = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=255, blank=True)
    receipt_name = models.CharField(max_length=160, blank=True)
    is_recurring = models.BooleanField(default=False)
    approved = models.BooleanField(default=False)
