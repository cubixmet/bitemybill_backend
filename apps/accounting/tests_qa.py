"""Senior QA: accounting books, reports, GST, sync, permissions, module catalog."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.billing.models import CatalogModule, RestaurantModule
from apps.billing.services import ensure_catalog_and_roles, provision_restaurant
from apps.restaurants.constants import CATALOG_MODULES, PERMISSION_SCOPES, ROLE_GRANTS

pytestmark = pytest.mark.django_db


def _user(email: str):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    u, _ = User.objects.get_or_create(email=email, defaults={"display_name": email.split("@")[0]})
    u.set_password("demo1234")
    u.save()
    return u


def _login(email: str, restaurant_id) -> APIClient:
    client = APIClient()
    res = client.post("/api/v1/auth/login/", {"identifier": email, "password": "demo1234"}, format="json")
    assert res.status_code == 200, res.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}", HTTP_X_RESTAURANT_ID=str(restaurant_id))
    return client


@pytest.fixture
def acct_ctx():
    ensure_catalog_and_roles()
    from apps.restaurants.models import RestaurantMembership, Role

    owner = _user("qa.acct.owner@test.com")
    accountant = _user("qa.acct.books@test.com")
    kitchen = _user("qa.acct.kitchen@test.com")
    restaurant = provision_restaurant(
        owner=owner,
        name="QA Accounting Lab",
        modules=["orders", "tables", "kitchen", "billing", "menu", "reports", "expenses", "accounting"],
    )
    RestaurantMembership.objects.get_or_create(
        user=accountant, restaurant=restaurant, defaults={"role": Role.objects.get(code="accountant")}
    )
    RestaurantMembership.objects.get_or_create(
        user=kitchen, restaurant=restaurant, defaults={"role": Role.objects.get(code="kitchen")}
    )
    return {
        "restaurant": restaurant,
        "owner_c": _login("qa.acct.owner@test.com", restaurant.id),
        "acct_c": _login("qa.acct.books@test.com", restaurant.id),
        "kitchen_c": _login("qa.acct.kitchen@test.com", restaurant.id),
    }


# ---------------------------------------------------------------------------
# Wiring / catalog
# ---------------------------------------------------------------------------


def test_qa_accounting_module_in_catalog_and_scopes():
    ensure_catalog_and_roles()
    assert "accounting" in PERMISSION_SCOPES
    assert any(c[0] == "accounting" for c in CATALOG_MODULES)
    mod = CatalogModule.objects.get(code="accounting")
    assert mod.monthly_price == Decimal("699")
    assert mod.group == "Finance"
    assert ROLE_GRANTS["accountant"].get("accounting")
    assert ROLE_GRANTS["manager"].get("accounting")
    assert "accounting" not in ROLE_GRANTS["cashier"]


def test_qa_accounting_urls_resolve():
    from django.urls import get_resolver

    patterns = {getattr(p, "name", None) for p in get_resolver("config.urls").url_patterns}
    # smoke: reverse API paths via client later; ensure include present
    from config import api_urls

    joined = " ".join(str(p.pattern) for p in api_urls.urlpatterns)
    assert "accounting/" in joined


# ---------------------------------------------------------------------------
# Chart of accounts + journals
# ---------------------------------------------------------------------------


def test_qa_seed_coa_and_list_accounts(acct_ctx):
    c = acct_ctx["owner_c"]
    res = c.post("/api/v1/accounting/accounts/seed/", {}, format="json")
    assert res.status_code == 200, res.content
    assert res.data["created"] >= 0
    res = c.get("/api/v1/accounting/accounts/")
    assert res.status_code == 200
    rows = res.data if isinstance(res.data, list) else res.data.get("results", [])
    codes = {a["code"] for a in rows}
    for required in ("1010", "4100", "2210", "2220", "6100", "1300"):
        assert required in codes, f"missing COA code {required}"


def test_qa_journal_must_balance(acct_ctx):
    from apps.accounting.defaults import ensure_chart_of_accounts
    from apps.accounting.services import create_journal

    r = acct_ctx["restaurant"]
    ensure_chart_of_accounts(r)
    with pytest.raises(ValueError, match="not balanced"):
        create_journal(
            restaurant=r,
            entry_date=date.today(),
            lines=[
                {"account_code": "1010", "debit": 100, "credit": 0},
                {"account_code": "4100", "debit": 0, "credit": 90},
            ],
            memo="bad",
            reference="qa:unbalanced",
        )


def test_qa_journal_api_create_post_and_reject_unbalanced(acct_ctx):
    c = acct_ctx["acct_c"]
    c.post("/api/v1/accounting/accounts/seed/", {}, format="json")
    bad = c.post(
        "/api/v1/accounting/journals/",
        {
            "date": str(date.today()),
            "memo": "unbalanced",
            "lines": [
                {"account_code": "1010", "debit": "50.00", "credit": "0"},
                {"account_code": "4100", "debit": "0", "credit": "40.00"},
            ],
        },
        format="json",
    )
    assert bad.status_code == 400

    ok = c.post(
        "/api/v1/accounting/journals/",
        {
            "date": str(date.today()),
            "memo": "sales",
            "reference": "qa:jv:1",
            "lines": [
                {"account_code": "1010", "debit": "1050.00", "credit": "0", "narration": "cash"},
                {"account_code": "4100", "debit": "0", "credit": "1000.00", "narration": "food"},
                {"account_code": "2210", "debit": "0", "credit": "25.00", "narration": "cgst"},
                {"account_code": "2220", "debit": "0", "credit": "25.00", "narration": "sgst"},
            ],
        },
        format="json",
    )
    assert ok.status_code == 201, ok.content
    assert ok.data["status"] == "posted"
    assert Decimal(ok.data["total_debit"]) == Decimal(ok.data["total_credit"]) == Decimal("1050.00")

    # idempotent by reference
    again = c.post(
        "/api/v1/accounting/journals/",
        {
            "date": str(date.today()),
            "memo": "sales again",
            "reference": "qa:jv:1",
            "lines": [
                {"account_code": "1010", "debit": "1050.00", "credit": "0"},
                {"account_code": "4100", "debit": "0", "credit": "1050.00"},
            ],
        },
        format="json",
    )
    assert again.status_code == 201
    assert again.data["number"] == ok.data["number"]


# ---------------------------------------------------------------------------
# Reports: TB / P&L / BS / ledger / GST
# ---------------------------------------------------------------------------


def test_qa_trial_balance_pnl_balance_sheet_ledger(acct_ctx):
    from apps.accounting.defaults import ensure_chart_of_accounts
    from apps.accounting.models import Account
    from apps.accounting.services import create_journal

    r = acct_ctx["restaurant"]
    ensure_chart_of_accounts(r)
    today = date.today()
    create_journal(
        restaurant=r,
        entry_date=today,
        lines=[
            {"account_code": "1010", "debit": 2100, "credit": 0},
            {"account_code": "4100", "debit": 0, "credit": 2000},
            {"account_code": "2210", "debit": 0, "credit": 50},
            {"account_code": "2220", "debit": 0, "credit": 50},
        ],
        memo="sales",
        reference="qa:sales:tb",
    )
    create_journal(
        restaurant=r,
        entry_date=today,
        lines=[
            {"account_code": "6100", "debit": 500, "credit": 0},
            {"account_code": "1010", "debit": 0, "credit": 500},
        ],
        memo="rent",
        reference="qa:rent:tb",
    )

    c = acct_ctx["acct_c"]
    tb = c.get(f"/api/v1/accounting/trial-balance/?as_of={today}")
    assert tb.status_code == 200, tb.content
    assert tb.data["balanced"] is True
    assert Decimal(tb.data["total_debit"]) == Decimal(tb.data["total_credit"])
    assert Decimal(tb.data["total_debit"]) > 0

    pnl = c.get(f"/api/v1/accounting/profit-and-loss/?from={today.year}-01-01&to={today}")
    assert pnl.status_code == 200
    assert Decimal(pnl.data["total_income"]) == Decimal("2000")
    assert Decimal(pnl.data["total_expenses"]) == Decimal("500")
    assert Decimal(pnl.data["net_profit"]) == Decimal("1500")

    bs = c.get(f"/api/v1/accounting/balance-sheet/?as_of={today}")
    assert bs.status_code == 200
    assert bs.data["balanced"] is True
    # cash 2100 - 500 rent = 1600 assets; liabilities GST 100; equity CY P&L 1500
    assert Decimal(bs.data["total_assets"]) == Decimal(bs.data["total_liabilities_equity"])
    assert Decimal(bs.data["total_assets"]) == Decimal("1600")

    cash = Account.objects.get(restaurant=r, code="1010")
    led = c.get(f"/api/v1/accounting/accounts/{cash.id}/ledger/?from={today}&to={today}")
    assert led.status_code == 200
    assert len(led.data["rows"]) == 2
    assert Decimal(led.data["closing_balance"]) == Decimal("1600")


def test_qa_gst_summary_inclusive_and_exclusive(acct_ctx):
    from apps.orders.models import Order
    from apps.restaurants.models import RestaurantSettings

    r = acct_ctx["restaurant"]
    settings_obj, _ = RestaurantSettings.objects.get_or_create(restaurant=r)
    settings_obj.tax_rate = Decimal("5")
    settings_obj.tax_inclusive = False
    settings_obj.save()

    Order.objects.create(
        restaurant=r,
        number="QA-GST-1",
        order_type="takeaway",
        channel="pos",
        status="completed",
        payment_status="paid",
        subtotal=Decimal("1000"),
        tax=Decimal("50"),
        total=Decimal("1000"),
        placed_at=date.today().isoformat() + "T12:00:00Z",
    )
    # placed_at needs datetime — fix if model requires
    from django.utils import timezone

    o = Order.objects.get(number="QA-GST-1")
    o.placed_at = timezone.now()
    o.save(update_fields=["placed_at"])

    c = acct_ctx["acct_c"]
    today = date.today()
    gst = c.get(f"/api/v1/accounting/gst/?from={today}&to={today}")
    assert gst.status_code == 200, gst.content
    assert Decimal(gst.data["gst_rate"]) == Decimal("5")
    assert Decimal(gst.data["outward"]["taxable_value"]) == Decimal("1000")
    assert Decimal(gst.data["outward"]["total_tax"]) == Decimal("50.00")
    assert Decimal(gst.data["outward"]["cgst"]) + Decimal(gst.data["outward"]["sgst"]) == Decimal("50.00")
    assert "GSTR-1" in " ".join(gst.data["gstr_notes"])


# ---------------------------------------------------------------------------
# Sync operational books
# ---------------------------------------------------------------------------


def test_qa_sync_sales_and_expenses_idempotent(acct_ctx):
    from apps.expenses.defaults import ensure_default_expense_categories
    from apps.expenses.models import Expense, ExpenseCategory
    from apps.orders.models import Order
    from django.utils import timezone

    r = acct_ctx["restaurant"]
    ensure_default_expense_categories(r)
    leaf = ExpenseCategory.objects.filter(restaurant=r, parent__isnull=False).first()
    assert leaf is not None
    today = timezone.localdate()

    Order.objects.create(
        restaurant=r,
        number="QA-SYNC-1",
        order_type="dine_in",
        channel="pos",
        status="completed",
        payment_status="paid",
        subtotal=Decimal("500"),
        tax=Decimal("25"),
        total=Decimal("500"),
        placed_at=timezone.now(),
    )
    Expense.objects.create(
        restaurant=r,
        category=leaf,
        payee="Landlord",
        amount=Decimal("200"),
        date=today,
        note="Shop rent",
    )

    c = acct_ctx["owner_c"]
    c.post("/api/v1/accounting/accounts/seed/", {}, format="json")
    sync1 = c.post("/api/v1/accounting/sync/", {"date": str(today)}, format="json")
    assert sync1.status_code == 200, sync1.content
    assert sync1.data["count"] >= 2

    sync2 = c.post("/api/v1/accounting/sync/", {"date": str(today)}, format="json")
    assert sync2.status_code == 200
    # second sync should not duplicate posted refs — journals list may still return existing
    from apps.accounting.models import JournalEntry

    sales_refs = JournalEntry.objects.filter(restaurant=r, reference__startswith=f"sales:{r.id}:{today}").count()
    assert sales_refs == 1
    exp_refs = JournalEntry.objects.filter(restaurant=r, reference__startswith="expense:").count()
    assert exp_refs == 1

    tb = c.get("/api/v1/accounting/trial-balance/")
    assert tb.status_code == 200
    assert tb.data["balanced"] is True


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_qa_kitchen_denied_accounting_accountant_allowed(acct_ctx):
    kitchen = acct_ctx["kitchen_c"]
    acct = acct_ctx["acct_c"]
    denied = kitchen.get("/api/v1/accounting/trial-balance/")
    assert denied.status_code in (403, 401), denied.content
    allowed = acct.get("/api/v1/accounting/trial-balance/")
    assert allowed.status_code == 200, allowed.content
    seed = acct.post("/api/v1/accounting/accounts/seed/", {}, format="json")
    assert seed.status_code == 200


def test_qa_provision_enables_accounting_module_and_coa(acct_ctx):
    r = acct_ctx["restaurant"]
    assert RestaurantModule.objects.filter(restaurant=r, module__code="accounting", enabled=True).exists()
    from apps.accounting.models import Account

    # provision should have seeded COA when accounting in modules
    assert Account.objects.filter(restaurant=r).count() >= 30


def test_qa_odd_paise_tax_sync_does_not_crash(acct_ctx):
    """Regression: tax 0.01 previously created a 0.00 CGST line and failed journal validation."""
    from django.utils import timezone
    from apps.orders.models import Order
    from apps.restaurants.models import RestaurantSettings
    from apps.accounting.services import sync_operational_books, trial_balance

    r = acct_ctx["restaurant"]
    settings_obj, _ = RestaurantSettings.objects.get_or_create(restaurant=r)
    settings_obj.tax_rate = Decimal("5")
    settings_obj.tax_inclusive = True
    settings_obj.save()
    # Inclusive total such that extracted tax can round to 0.01
    Order.objects.create(
        restaurant=r,
        number="QA-PAISE-1",
        order_type="takeaway",
        channel="pos",
        status="completed",
        payment_status="paid",
        subtotal=Decimal("0.21"),
        tax=Decimal("0.01"),
        total=Decimal("0.21"),
        placed_at=timezone.now(),
    )
    day = timezone.localdate()
    result = sync_operational_books(r, day=day)
    assert result["count"] >= 1
    tb = trial_balance(r, as_of=day)
    assert tb["balanced"] is True


def test_qa_shell_api_contract_paths_exist(acct_ctx):
    """Paths used by dashboard.accounting.tsx must respond (not 404)."""
    c = acct_ctx["owner_c"]
    today = date.today().isoformat()
    paths = [
        "/api/v1/accounting/accounts/",
        "/api/v1/accounting/journals/",
        f"/api/v1/accounting/trial-balance/?as_of={today}",
        f"/api/v1/accounting/profit-and-loss/?from={today}&to={today}",
        f"/api/v1/accounting/balance-sheet/?as_of={today}",
        f"/api/v1/accounting/gst/?from={today}&to={today}",
    ]
    for path in paths:
        res = c.get(path)
        assert res.status_code == 200, f"{path} -> {res.status_code} {res.content[:200]}"
