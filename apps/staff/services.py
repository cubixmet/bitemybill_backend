from __future__ import annotations

import calendar
import math
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.staff.models import (
    Attendance,
    DailyWageSettlement,
    PaymentStatus,
    PayrollPayment,
    PayrollRun,
    Payslip,
    StaffProfile,
)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _month_bounds(period: str) -> tuple[date, date]:
    """period is YYYY-MM."""
    year, month = [int(x) for x in period.split("-")[:2]]
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start, end


def _weekdays(start: date, end: date) -> int:
    n = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            n += 1
        cur = date.fromordinal(cur.toordinal() + 1)
    return max(n, 1)


def _attendance_credit(mark: str) -> Decimal:
    if mark in (Attendance.Mark.PRESENT, Attendance.Mark.LATE):
        return Decimal("1")
    if mark == Attendance.Mark.HALF_DAY:
        return Decimal("0.5")
    return Decimal("0")


@transaction.atomic
def generate_daily_wages(*, restaurant, day: date | None = None, staff_ids: list | None = None) -> list[DailyWageSettlement]:
    """Auto-create daily wage settlements from attendance for daily-wage staff."""
    day = day or timezone.localdate()
    qs = StaffProfile.objects.filter(restaurant=restaurant, is_active=True, wage_type="daily", deleted_at__isnull=True)
    if staff_ids:
        qs = qs.filter(id__in=staff_ids)
    created: list[DailyWageSettlement] = []
    for staff in qs:
        att = Attendance.objects.filter(restaurant=restaurant, staff=staff, date=day).first()
        if not att:
            continue
        credit = _attendance_credit(att.mark)
        if credit <= 0:
            continue
        amount = (staff.wage_amount * credit).quantize(Decimal("0.01"))
        obj, was_created = DailyWageSettlement.objects.get_or_create(
            restaurant=restaurant,
            staff=staff,
            date=day,
            defaults={"amount": amount, "approved": False, "source": "auto", "days": credit},
        )
        if was_created:
            created.append(obj)
        elif obj.source == "auto" and not obj.approved:
            obj.amount = amount
            obj.days = credit
            obj.save(update_fields=["amount", "days"])
            created.append(obj)
    return created


@transaction.atomic
def create_manual_wage(
    *,
    restaurant,
    staff: StaffProfile,
    day: date,
    amount: Decimal,
    days: Decimal | None = None,
) -> DailyWageSettlement:
    obj, _ = DailyWageSettlement.objects.update_or_create(
        restaurant=restaurant,
        staff=staff,
        date=day,
        defaults={
            "amount": amount,
            "approved": False,
            "source": "manual",
            "days": days if days is not None else Decimal("1"),
        },
    )
    return obj


@transaction.atomic
def generate_monthly_payroll(
    *,
    restaurant,
    period: str | None = None,
    mode: str = "auto",
    slips: list[dict] | None = None,
) -> PayrollRun:
    """
    mode=auto  → build payslips for monthly-salary staff from wage_amount + attendance.
    mode=manual → build from provided slips [{staff, gross, deductions, allowance, overtime, bonus, advance}].
    """
    if not period:
        today = timezone.localdate()
        period = f"{today.year:04d}-{today.month:02d}"

    run, _ = PayrollRun.objects.get_or_create(
        restaurant=restaurant,
        period=period,
        defaults={"status": PayrollRun.Status.DRAFT, "source": mode, "total": 0},
    )
    if run.status == PayrollRun.Status.PAID or run.amount_paid > 0:
        raise ValueError("Payroll for this period already has payments — settle remaining slips instead")

    run.source = mode
    run.status = PayrollRun.Status.DRAFT
    run.slips.all().delete()

    total = Decimal("0")
    if mode == "manual":
        for row in slips or []:
            staff = StaffProfile.objects.get(pk=row["staff"], restaurant=restaurant)
            allowance = Decimal(str(row.get("allowance") or 0))
            overtime = Decimal(str(row.get("overtime") or 0))
            bonus = Decimal(str(row.get("bonus") or 0))
            deduction = Decimal(str(row.get("deductions") or row.get("deduction") or 0))
            advance = Decimal(str(row.get("advance") or 0))
            gross = Decimal(str(row.get("gross") or row.get("basic") or staff.wage_amount))
            net = gross + allowance + overtime + bonus - deduction - advance
            Payslip.objects.create(
                run=run,
                staff=staff,
                gross=gross,
                deductions=deduction + advance,
                net=net,
                allowance=allowance,
                overtime=overtime,
                bonus=bonus,
                advance=advance,
                wage_basis=staff.wage_type,
                days_worked=Decimal(str(row.get("days_worked") or 0)),
                source="manual",
            )
            total += net
    else:
        start, end = _month_bounds(period)
        expected = Decimal(_weekdays(start, end))
        staff_qs = StaffProfile.objects.filter(
            restaurant=restaurant, is_active=True, wage_type="monthly", deleted_at__isnull=True
        )
        for staff in staff_qs:
            marks = list(
                Attendance.objects.filter(restaurant=restaurant, staff=staff, date__gte=start, date__lte=end)
            )
            # Full monthly salary minus unpaid absences / half-days.
            # Unmarked days stay paid (typical mid-month / incomplete roster).
            unpaid = Decimal("0")
            for m in marks:
                if m.mark == Attendance.Mark.ABSENT:
                    unpaid += Decimal("1")
                elif m.mark == Attendance.Mark.HALF_DAY:
                    unpaid += Decimal("0.5")
            days = (expected - unpaid).quantize(Decimal("0.01"))
            if days < 0:
                days = Decimal("0")
            ratio = min(days / expected, Decimal("1"))
            gross = (staff.wage_amount * ratio).quantize(Decimal("0.01"))
            net = gross
            Payslip.objects.create(
                run=run,
                staff=staff,
                gross=gross,
                deductions=Decimal("0"),
                net=net,
                allowance=Decimal("0"),
                overtime=Decimal("0"),
                bonus=Decimal("0"),
                advance=Decimal("0"),
                wage_basis="monthly",
                days_worked=days,
                source="auto",
            )
            total += net

    run.total = total
    run.save(update_fields=["total", "status", "source", "updated_at"])
    return run


def parse_period(value: str | None) -> str:
    if value:
        return value
    today = timezone.localdate()
    return f"{today.year:04d}-{today.month:02d}"


def parse_day(value: str | None) -> date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return timezone.localdate()


def _payment_status(amount: Decimal, paid: Decimal) -> str:
    if paid <= 0:
        return PaymentStatus.UNPAID
    if paid + Decimal("0.009") >= amount:
        return PaymentStatus.PAID
    return PaymentStatus.PARTIAL


def refresh_payroll_run_payment(run: PayrollRun) -> PayrollRun:
    # Avoid stale prefetch_related cache from the viewset queryset.
    slips = list(Payslip.objects.filter(run_id=run.id))
    paid = sum((s.amount_paid for s in slips), Decimal("0"))
    run.amount_paid = paid
    if not slips:
        pass
    elif all(s.payment_status == PaymentStatus.PAID for s in slips):
        run.status = PayrollRun.Status.PAID
    elif any(s.amount_paid > 0 for s in slips):
        run.status = PayrollRun.Status.PARTIAL
    elif run.status in (PayrollRun.Status.PAID, PayrollRun.Status.PARTIAL):
        run.status = PayrollRun.Status.APPROVED
    run.save(update_fields=["amount_paid", "status", "updated_at"])
    return run


@transaction.atomic
def settle_payslip(
    *,
    restaurant,
    slip: Payslip,
    amount: Decimal,
    method: str = "Cash",
    note: str = "",
    user=None,
) -> Payslip:
    if slip.run.status == PayrollRun.Status.DRAFT:
        raise ValueError("Approve payroll before recording payments")
    if amount <= 0:
        raise ValueError("Payment amount must be positive")
    pending = slip.pending_amount
    if amount > pending + Decimal("0.01"):
        raise ValueError(f"Amount exceeds pending {pending}")
    pay = min(amount, pending)
    slip.amount_paid = (slip.amount_paid + pay).quantize(Decimal("0.01"))
    slip.payment_status = _payment_status(slip.net, slip.amount_paid)
    if slip.payment_status == PaymentStatus.PAID:
        slip.amount_paid = slip.net
    slip.save(update_fields=["amount_paid", "payment_status", "updated_at"])
    PayrollPayment.objects.create(
        restaurant=restaurant,
        payslip=slip,
        amount=pay,
        method=method or "Cash",
        note=note or "",
        created_by=user,
    )
    refresh_payroll_run_payment(slip.run)
    return slip


@transaction.atomic
def settle_daily_wage(
    *,
    restaurant,
    wage: DailyWageSettlement,
    amount: Decimal,
    method: str = "Cash",
    note: str = "",
    user=None,
) -> DailyWageSettlement:
    if not wage.approved:
        raise ValueError("Approve the wage settlement before paying")
    if amount <= 0:
        raise ValueError("Payment amount must be positive")
    pending = wage.pending_amount
    if amount > pending + Decimal("0.01"):
        raise ValueError(f"Amount exceeds pending {pending}")
    pay = min(amount, pending)
    wage.amount_paid = (wage.amount_paid + pay).quantize(Decimal("0.01"))
    wage.payment_status = _payment_status(wage.amount, wage.amount_paid)
    if wage.payment_status == PaymentStatus.PAID:
        wage.amount_paid = wage.amount
    wage.save(update_fields=["amount_paid", "payment_status", "updated_at"])
    PayrollPayment.objects.create(
        restaurant=restaurant,
        daily_wage=wage,
        amount=pay,
        method=method or "Cash",
        note=note or "",
        created_by=user,
    )
    return wage
