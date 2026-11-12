from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.common.models import RestaurantOwnedModel, TimeStampedModel


class StaffProfile(RestaurantOwnedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff_profiles"
    )
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    role_title = models.CharField(max_length=80, blank=True)
    wage_type = models.CharField(max_length=20, default="monthly")  # monthly, daily
    wage_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    joined_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)


class Attendance(RestaurantOwnedModel):
    class Mark(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LEAVE = "leave", "Leave"
        HALF_DAY = "half_day", "Half Day"
        LATE = "late", "Late"

    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    mark = models.CharField(max_length=16, choices=Mark.choices, default=Mark.PRESENT)
    note = models.CharField(max_length=255, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    distance_m = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ("staff", "date")


class PaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PARTIAL = "partial", "Partial"
    PAID = "paid", "Paid"


class DailyWageSettlement(RestaurantOwnedModel):
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="daily_wages")
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    approved = models.BooleanField(default=False)
    source = models.CharField(max_length=16, default="manual")  # auto | manual
    days = models.DecimalField(max_digits=5, decimal_places=2, default=1)
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def pending_amount(self) -> Decimal:
        pending = self.amount - self.amount_paid
        return pending if pending > 0 else Decimal("0")


class PayrollRun(RestaurantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        PARTIAL = "partial", "Partially paid"
        PAID = "paid", "Paid"

    period = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    source = models.CharField(max_length=16, default="auto")  # auto | manual
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("restaurant", "period")

    @property
    def pending_amount(self) -> Decimal:
        pending = self.total - self.amount_paid
        return pending if pending > 0 else Decimal("0")


class Payslip(TimeStampedModel):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="slips")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE)
    gross = models.DecimalField(max_digits=12, decimal_places=2)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=12, decimal_places=2)
    allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    overtime = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    advance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    wage_basis = models.CharField(max_length=20, default="monthly")
    days_worked = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    source = models.CharField(max_length=16, default="auto")
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def pending_amount(self) -> Decimal:
        pending = self.net - self.amount_paid
        return pending if pending > 0 else Decimal("0")


class PayrollPayment(TimeStampedModel):
    """Partial or full settlement against a payslip or daily wage."""

    restaurant = models.ForeignKey(
        "restaurants.Restaurant", on_delete=models.CASCADE, related_name="payroll_payments"
    )
    payslip = models.ForeignKey(Payslip, null=True, blank=True, on_delete=models.CASCADE, related_name="payments")
    daily_wage = models.ForeignKey(
        DailyWageSettlement, null=True, blank=True, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=40, blank=True, default="Cash")
    note = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
