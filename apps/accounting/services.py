from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.accounting.defaults import ensure_chart_of_accounts
from apps.accounting.models import Account, JournalEntry, JournalLine


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _line_totals_by_account(
    restaurant,
    *,
    as_of: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    account_ids=None,
) -> dict:
    """One grouped query: account_id → {debit, credit} for posted lines."""
    qs = JournalLine.objects.filter(
        entry__restaurant=restaurant,
        entry__status=JournalEntry.Status.POSTED,
    )
    if account_ids is not None:
        qs = qs.filter(account_id__in=account_ids)
    if as_of is not None:
        qs = qs.filter(entry__date__lte=as_of)
    if date_from is not None:
        qs = qs.filter(entry__date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(entry__date__lte=date_to)
    rows = qs.values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))
    return {row["account_id"]: {"d": _d(row["d"]), "c": _d(row["c"])} for row in rows}


def next_journal_number(restaurant) -> str:
    today = timezone.localdate()
    prefix = f"JV-{today.strftime('%Y%m')}-"
    last = (
        JournalEntry.objects.filter(restaurant=restaurant, number__startswith=prefix)
        .order_by("-number")
        .values_list("number", flat=True)
        .first()
    )
    seq = 1
    if last:
        try:
            seq = int(last.split("-")[-1]) + 1
        except ValueError:
            seq = JournalEntry.objects.filter(restaurant=restaurant).count() + 1
    return f"{prefix}{seq:04d}"


@transaction.atomic
def create_journal(
    *,
    restaurant,
    entry_date: date,
    lines: list[dict],
    memo: str = "",
    reference: str = "",
    number: str | None = None,
    post: bool = True,
    user=None,
) -> JournalEntry:
    """
    lines: [{account_id|account_code, debit, credit, narration?}]
    """
    ensure_chart_of_accounts(restaurant)
    if reference:
        existing = JournalEntry.objects.filter(
            restaurant=restaurant, reference=reference, status=JournalEntry.Status.POSTED
        ).first()
        if existing:
            return existing

    prepared = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for row in lines:
        debit = _d(row.get("debit"))
        credit = _d(row.get("credit"))
        # Skip blank amounts (e.g. CGST half rounded to 0.00) without failing the voucher
        if debit == 0 and credit == 0:
            continue
        account = None
        if row.get("account_id"):
            account = Account.objects.filter(restaurant=restaurant, pk=row["account_id"]).first()
        elif row.get("account_code"):
            account = Account.objects.filter(restaurant=restaurant, code=row["account_code"]).first()
        if not account:
            raise ValueError(f"Account not found for line {row}")
        if debit < 0 or credit < 0:
            raise ValueError("Amounts must be non-negative")
        if debit > 0 and credit > 0:
            raise ValueError("A line cannot have both debit and credit")
        total_debit += debit
        total_credit += credit
        prepared.append((account, debit, credit, row.get("narration") or ""))

    if total_debit != total_credit:
        raise ValueError(f"Journal not balanced: debit {total_debit} ≠ credit {total_credit}")
    if total_debit == 0:
        raise ValueError("Journal has no amounts")

    entry = JournalEntry.objects.create(
        restaurant=restaurant,
        number=number or next_journal_number(restaurant),
        date=entry_date,
        memo=memo or "",
        reference=reference or "",
        status=JournalEntry.Status.DRAFT,
    )
    for account, debit, credit, narration in prepared:
        JournalLine.objects.create(
            entry=entry, account=account, debit=debit, credit=credit, narration=narration
        )
    if post:
        post_journal(entry, user=user)
    return entry


@transaction.atomic
def post_journal(entry: JournalEntry, user=None) -> JournalEntry:
    if entry.status == JournalEntry.Status.POSTED:
        return entry
    if entry.status == JournalEntry.Status.VOID:
        raise ValueError("Cannot post a void journal")
    if entry.total_debit != entry.total_credit:
        raise ValueError("Cannot post unbalanced journal")
    entry.status = JournalEntry.Status.POSTED
    entry.posted_at = timezone.now()
    entry.posted_by = user
    entry.save(update_fields=["status", "posted_at", "posted_by", "updated_at"])
    return entry


def account_balance(account: Account, *, as_of: date | None = None, posted_only: bool = True) -> Decimal:
    qs = JournalLine.objects.filter(account=account)
    if posted_only:
        qs = qs.filter(entry__status=JournalEntry.Status.POSTED)
    if as_of:
        qs = qs.filter(entry__date__lte=as_of)
    agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    debit = _d(agg["d"])
    credit = _d(agg["c"])
    # Assets & expenses: debit-normal; liabilities, equity, income: credit-normal
    if account.account_type in (Account.Type.ASSET, Account.Type.EXPENSE):
        return debit - credit
    return credit - debit


def trial_balance(restaurant, *, as_of: date | None = None, _ensured: bool = False) -> dict:
    if not _ensured:
        ensure_chart_of_accounts(restaurant)
    as_of = as_of or timezone.localdate()
    accounts = list(Account.objects.filter(restaurant=restaurant, is_active=True).order_by("code"))
    totals_by = _line_totals_by_account(restaurant, as_of=as_of, account_ids=[a.id for a in accounts])
    rows = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for account in accounts:
        agg = totals_by.get(account.id, {"d": Decimal("0"), "c": Decimal("0")})
        debit, credit = agg["d"], agg["c"]
        if debit == 0 and credit == 0:
            continue
        # Present TB as debit/credit columns (net side)
        if account.account_type in (Account.Type.ASSET, Account.Type.EXPENSE):
            net = debit - credit
            d, c = (net, Decimal("0")) if net >= 0 else (Decimal("0"), abs(net))
        else:
            net = credit - debit
            d, c = (Decimal("0"), net) if net >= 0 else (abs(net), Decimal("0"))
        total_debit += d
        total_credit += c
        rows.append(
            {
                "account_id": str(account.id),
                "code": account.code,
                "name": account.name,
                "account_type": account.account_type,
                "debit": d,
                "credit": c,
            }
        )
    return {
        "as_of": str(as_of),
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }


def profit_and_loss(restaurant, *, date_from: date, date_to: date, _ensured: bool = False) -> dict:
    if not _ensured:
        ensure_chart_of_accounts(restaurant)
    accounts = list(
        Account.objects.filter(
            restaurant=restaurant, is_active=True, account_type__in=[Account.Type.INCOME, Account.Type.EXPENSE]
        ).order_by("code")
    )
    totals_by = _line_totals_by_account(
        restaurant, date_from=date_from, date_to=date_to, account_ids=[a.id for a in accounts]
    )
    income_rows = []
    expense_rows = []
    income_total = Decimal("0")
    expense_total = Decimal("0")
    for account in accounts:
        agg = totals_by.get(account.id, {"d": Decimal("0"), "c": Decimal("0")})
        debit, credit = agg["d"], agg["c"]
        if account.account_type == Account.Type.INCOME:
            amount = credit - debit
            if amount == 0:
                continue
            income_total += amount
            income_rows.append({"code": account.code, "name": account.name, "amount": amount})
        else:
            amount = debit - credit
            if amount == 0:
                continue
            expense_total += amount
            expense_rows.append({"code": account.code, "name": account.name, "amount": amount})
    return {
        "from": str(date_from),
        "to": str(date_to),
        "income": income_rows,
        "expenses": expense_rows,
        "total_income": income_total,
        "total_expenses": expense_total,
        "net_profit": income_total - expense_total,
    }


def balance_sheet(restaurant, *, as_of: date | None = None) -> dict:
    ensure_chart_of_accounts(restaurant)
    as_of = as_of or timezone.localdate()
    accounts = list(
        Account.objects.filter(
            restaurant=restaurant,
            is_active=True,
            account_type__in=[Account.Type.ASSET, Account.Type.LIABILITY, Account.Type.EQUITY],
        ).order_by("code")
    )
    totals_by = _line_totals_by_account(restaurant, as_of=as_of, account_ids=[a.id for a in accounts])
    sections = {"asset": [], "liability": [], "equity": []}
    totals = {"asset": Decimal("0"), "liability": Decimal("0"), "equity": Decimal("0")}
    for account in accounts:
        agg = totals_by.get(account.id, {"d": Decimal("0"), "c": Decimal("0")})
        debit, credit = agg["d"], agg["c"]
        if account.account_type in (Account.Type.ASSET, Account.Type.EXPENSE):
            bal = debit - credit
        else:
            bal = credit - debit
        if bal == 0:
            continue
        sections[account.account_type].append({"code": account.code, "name": account.name, "amount": bal})
        totals[account.account_type] += bal

    # Close P&L into equity for BS presentation (COA already ensured above)
    pnl = profit_and_loss(
        restaurant,
        date_from=date(as_of.year, 1, 1),
        date_to=as_of,
        _ensured=True,
    )
    net = _d(pnl["net_profit"])
    if net != 0:
        sections["equity"].append({"code": "3200", "name": "Current Year P&L", "amount": net})
        totals["equity"] += net

    return {
        "as_of": str(as_of),
        "assets": sections["asset"],
        "liabilities": sections["liability"],
        "equity": sections["equity"],
        "total_assets": totals["asset"],
        "total_liabilities": totals["liability"],
        "total_equity": totals["equity"],
        "total_liabilities_equity": totals["liability"] + totals["equity"],
        "balanced": totals["asset"] == (totals["liability"] + totals["equity"]),
    }


def ledger_for_account(restaurant, account: Account, *, date_from: date | None = None, date_to: date | None = None) -> dict:
    qs = JournalLine.objects.filter(
        account=account, entry__restaurant=restaurant, entry__status=JournalEntry.Status.POSTED
    ).select_related("entry")
    if date_from:
        qs = qs.filter(entry__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__date__lte=date_to)
    qs = qs.order_by("entry__date", "created_at")
    running = Decimal("0")
    rows = []
    for line in qs:
        if account.account_type in (Account.Type.ASSET, Account.Type.EXPENSE):
            running += line.debit - line.credit
        else:
            running += line.credit - line.debit
        rows.append(
            {
                "id": str(line.id),
                "date": str(line.entry.date),
                "entry_id": str(line.entry_id),
                "number": line.entry.number,
                "memo": line.entry.memo,
                "narration": line.narration,
                "debit": line.debit,
                "credit": line.credit,
                "balance": running,
            }
        )
    return {
        "account": {"id": str(account.id), "code": account.code, "name": account.name, "type": account.account_type},
        "from": str(date_from) if date_from else None,
        "to": str(date_to) if date_to else None,
        "rows": rows,
        "closing_balance": running,
    }


def gst_summary(restaurant, *, date_from: date, date_to: date) -> dict:
    """
    GST summary from posted sales journals + taxable sales estimate from orders.
    Splits CGST/SGST equally for intra-state (Kerala default).
    """
    from apps.orders.models import Order

    settings_obj = getattr(restaurant, "settings", None)
    rate = _d(getattr(settings_obj, "tax_rate", None) or 5)
    inclusive = bool(getattr(settings_obj, "tax_inclusive", False))

    orders = Order.objects.filter(
        restaurant=restaurant,
        placed_at__date__gte=date_from,
        placed_at__date__lte=date_to,
    ).exclude(status="cancelled")
    order_agg = orders.aggregate(s=Sum("total"), n=Count("id"))
    taxable_sales = _d(order_agg["s"])
    invoice_count = int(order_agg["n"] or 0)

    if inclusive and rate > 0:
        # extract tax from inclusive total
        base = (taxable_sales * 100 / (100 + rate)).quantize(Decimal("0.01"))
        output_tax = taxable_sales - base
        taxable_value = base
    else:
        taxable_value = taxable_sales
        output_tax = (taxable_sales * rate / Decimal("100")).quantize(Decimal("0.01"))

    half = (output_tax / 2).quantize(Decimal("0.01"))
    # Input GST from Input GST Credit account movements in period
    input_acc = Account.objects.filter(restaurant=restaurant, code="1300").first()
    input_tax = Decimal("0")
    if input_acc:
        qs = JournalLine.objects.filter(
            account=input_acc,
            entry__status=JournalEntry.Status.POSTED,
            entry__date__gte=date_from,
            entry__date__lte=date_to,
        )
        agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
        input_tax = _d(agg["d"]) - _d(agg["c"])
        if input_tax < 0:
            input_tax = Decimal("0")

    net_payable = output_tax - input_tax
    return {
        "from": str(date_from),
        "to": str(date_to),
        "gst_rate": rate,
        "tax_inclusive": inclusive,
        "outward": {
            "taxable_value": taxable_value,
            "cgst": half,
            "sgst": half,
            "igst": Decimal("0"),
            "total_tax": output_tax,
            "invoice_count": invoice_count,
        },
        "inward": {
            "input_tax_credit": input_tax,
            "cgst": (input_tax / 2).quantize(Decimal("0.01")),
            "sgst": (input_tax / 2).quantize(Decimal("0.01")),
        },
        "net_payable": net_payable,
        "gstr_notes": [
            "GSTR-1: report outward taxable supplies (Food/Beverage/Delivery sales).",
            "GSTR-3B: pay net GST (output − input credit) for the period.",
            "Intra-state Kerala assumed — tax split equally as CGST + SGST.",
        ],
    }


@transaction.atomic
def sync_operational_books(restaurant, *, day: date | None = None) -> dict:
    """
    Post daily sales & expenses into the ledger (idempotent by reference).
    Senior books stay reconciled with POS / expense modules.
    """
    from apps.expenses.models import Expense
    from apps.orders.models import Order

    ensure_chart_of_accounts(restaurant)
    day = day or timezone.localdate()
    created = []

    sales = Order.objects.filter(restaurant=restaurant, placed_at__date=day).exclude(status="cancelled")
    sales_total = _d(sales.aggregate(s=Sum("total"))["s"])
    if sales_total > 0:
        settings_obj = getattr(restaurant, "settings", None)
        rate = _d(getattr(settings_obj, "tax_rate", None) or 5)
        inclusive = bool(getattr(settings_obj, "tax_inclusive", False))
        if inclusive and rate > 0:
            base = (sales_total * 100 / (100 + rate)).quantize(Decimal("0.01"))
            tax = sales_total - base
            cash = sales_total
        else:
            base = sales_total
            tax = (sales_total * rate / Decimal("100")).quantize(Decimal("0.01"))
            cash = base + tax
        half = (tax / 2).quantize(Decimal("0.01"))
        cgst, sgst = half, (tax - half)
        lines = [
            {"account_code": "1010", "debit": cash, "credit": 0, "narration": "Daily sales collection"},
            {"account_code": "4100", "debit": 0, "credit": base, "narration": "Food sales"},
        ]
        # Skip 0.00 halves (odd paise) — create_journal also ignores blank lines
        if cgst > 0:
            lines.append({"account_code": "2210", "debit": 0, "credit": cgst, "narration": "CGST"})
        if sgst > 0:
            lines.append({"account_code": "2220", "debit": 0, "credit": sgst, "narration": "SGST"})
        entry = create_journal(
            restaurant=restaurant,
            entry_date=day,
            lines=lines,
            memo=f"POS sales {day}",
            reference=f"sales:{restaurant.id}:{day}",
            post=True,
        )
        created.append(entry.number)

    for exp in Expense.objects.filter(restaurant=restaurant, date=day):
        code = "6900"
        path = (exp.category.full_path if exp.category_id else "").lower()
        name = (exp.category.name if exp.category_id else "").lower()
        blob = f"{path} {name}"
        if "rent" in blob:
            code = "6100"
        elif any(x in blob for x in ("electric", "water", "gas", "internet", "utilit")):
            code = "6200"
        elif any(x in blob for x in ("salary", "wage", "payroll", "people")):
            code = "6300"
        elif "market" in blob or "ads" in blob:
            code = "6400"
        elif "maint" in blob or "repair" in blob or "clean" in blob:
            code = "6500"
        elif "tax" in blob or "gst" in blob:
            code = "6600"
        elif "event" in blob or "cater" in blob:
            code = "6700"
        entry = create_journal(
            restaurant=restaurant,
            entry_date=day,
            lines=[
                {"account_code": code, "debit": exp.amount, "credit": 0, "narration": exp.payee},
                {"account_code": "1010", "debit": 0, "credit": exp.amount, "narration": f"Paid {exp.payee}"},
            ],
            memo=exp.note or exp.payee,
            reference=f"expense:{exp.id}",
            post=True,
        )
        created.append(entry.number)

    return {"date": str(day), "journals": created, "count": len(created)}


def parse_day(value: str | None) -> date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return timezone.localdate()
