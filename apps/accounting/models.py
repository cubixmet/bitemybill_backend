from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.common.models import RestaurantOwnedModel, TimeStampedModel


class Account(RestaurantOwnedModel):
    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    account_type = models.CharField(max_length=20, choices=Type.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    gst_applicable = models.BooleanField(default=False)

    class Meta:
        ordering = ["code"]
        unique_together = ("restaurant", "code")

    def __str__(self):
        return f"{self.code} · {self.name}"


class JournalEntry(RestaurantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        VOID = "void", "Void"

    number = models.CharField(max_length=32, db_index=True)
    date = models.DateField()
    memo = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    reference = models.CharField(max_length=64, blank=True, db_index=True)  # e.g. order:UUID / expense:UUID
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = ("restaurant", "number")
        indexes = [
            models.Index(fields=["restaurant", "status", "date"], name="acct_je_rest_status_date"),
        ]

    def _line_totals(self) -> tuple[Decimal, Decimal]:
        cached = getattr(self, "_line_totals_cache", None)
        if cached is not None:
            return cached
        debit = credit = Decimal("0")
        for line in self.lines.all():  # uses prefetch when present
            debit += line.debit
            credit += line.credit
        self._line_totals_cache = (debit, credit)
        return self._line_totals_cache

    @property
    def total_debit(self) -> Decimal:
        return self._line_totals()[0]

    @property
    def total_credit(self) -> Decimal:
        return self._line_totals()[1]


class JournalLine(TimeStampedModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    narration = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["account", "entry"], name="acct_jl_account_entry"),
        ]

    def clean(self):
        if self.debit < 0 or self.credit < 0:
            raise ValidationError("Debit/credit cannot be negative")
        if self.debit > 0 and self.credit > 0:
            raise ValidationError("A line cannot have both debit and credit")
        if self.debit == 0 and self.credit == 0:
            raise ValidationError("Line needs a debit or credit amount")
