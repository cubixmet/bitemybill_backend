"""Helpers for API performance baselines (query counts + JSON snapshots)."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from django.db import connection, reset_queries
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient


def json_normalize(data: Any) -> Any:
    """Stable JSON-comparable structure (Decimals → str, preserve key order of dicts)."""
    return json.loads(json.dumps(data, default=str))


_UUID_RE = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    __import__("re").I,
)


def contract_shape(data: Any) -> Any:
    """
    Compare API contracts without brittle per-run UUIDs/timestamps.
    - UUID strings → "<uuid>"
    - ISO datetimes → "<datetime>"
    - list lengths preserved; first element shape used when list non-empty
    """
    if isinstance(data, dict):
        return {k: contract_shape(v) for k, v in data.items()}
    if isinstance(data, list):
        if not data:
            return []
        # Preserve length + representative element shapes (and full shapes for small lists)
        if len(data) <= 3:
            return [contract_shape(x) for x in data]
        return {"__len__": len(data), "__item__": contract_shape(data[0])}
    if isinstance(data, str):
        if _UUID_RE.match(data):
            return "<uuid>"
        if len(data) >= 10 and "T" in data and data[:4].isdigit():
            return "<datetime>"
        return data
    if isinstance(data, bool) or data is None:
        return data
    if isinstance(data, (int, float)):
        return data
    return data


def snapshot_get(client: APIClient, path: str) -> tuple[int, Any, int, float]:
    """
    Return (status_code, normalized_json, query_count, elapsed_ms).
    Requires DEBUG or force_debug_cursor for query capture in CaptureQueriesContext.
    """
    import time

    reset_queries()
    t0 = time.perf_counter()
    with CaptureQueriesContext(connection) as ctx:
        res = client.get(path)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    try:
        body = res.json()
    except Exception:
        body = res.content.decode("utf-8", errors="replace")
    return res.status_code, json_normalize(body), len(ctx), elapsed_ms


def seed_perf_restaurant(*, email: str = "perf.owner@test.com", name: str = "Perf Lab"):
    """Create a restaurant with bulk-related rows so N+1 shows up clearly."""
    from decimal import Decimal

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from apps.billing.services import ensure_catalog_and_roles, provision_restaurant
    from apps.inventory.models import InventoryItem, Supplier, Unit
    from apps.menu.models import MenuCategory, MenuItem, MenuModifierGroup, MenuModifierOption
    from apps.orders.models import DiningTable, Order, OrderItem, OrderItemModifier
    from apps.restaurants.models import RestaurantMembership, Role
    from apps.staff.models import StaffProfile
    from apps.todos.models import Todo
    from apps.customers.models import CustomerAddress, RestaurantCustomer
    from apps.expenses.defaults import ensure_default_expense_categories
    from apps.accounting.defaults import ensure_chart_of_accounts
    from apps.accounting.services import create_journal
    from apps.audit.models import AuditLog

    ensure_catalog_and_roles()
    User = get_user_model()
    owner, _ = User.objects.get_or_create(email=email, defaults={"display_name": "Perf Owner"})
    owner.set_password("demo1234")
    owner.save()
    restaurant = provision_restaurant(owner=owner, name=name)
    ensure_default_expense_categories(restaurant)
    ensure_chart_of_accounts(restaurant)

    kg, _ = Unit.objects.get_or_create(
        code="perf_kg", defaults={"name": "Kg", "kind": "mass", "to_base": Decimal("1")}
    )

    # Menu: 10 categories × 8 items × modifiers → nested N+1 magnet
    for c in range(10):
        cat = MenuCategory.objects.create(
            restaurant=restaurant, name=f"Perf Cat {c}", slug=f"perf-cat-{c}", sort_order=c
        )
        for i in range(8):
            item = MenuItem.objects.create(
                restaurant=restaurant,
                category=cat,
                name=f"Perf Item {c}-{i}",
                price=Decimal("100.00") + i,
                cost=Decimal("40.00"),
                station="Hot",
            )
            grp = MenuModifierGroup.objects.create(
                restaurant=restaurant, item=item, name="Size", required=False, max_select=1
            )
            MenuModifierOption.objects.create(
                restaurant=restaurant, group=grp, name="Regular", price=Decimal("0")
            )
            MenuModifierOption.objects.create(
                restaurant=restaurant, group=grp, name="Large", price=Decimal("30")
            )

    # Inventory + suppliers
    for s in range(20):
        Supplier.objects.create(restaurant=restaurant, name=f"Perf Supplier {s}", phone=f"90000000{s:02d}")
    for i in range(40):
        InventoryItem.objects.create(
            restaurant=restaurant,
            name=f"Perf Stock {i}",
            unit=kg,
            quantity=Decimal("10"),
            cost=Decimal("25"),
        )

    # Tables + orders
    for t in range(12):
        DiningTable.objects.create(restaurant=restaurant, code=f"T{t+1}", zone="Hall", seats=4)
    for o in range(60):
        order = Order.objects.create(
            restaurant=restaurant,
            number=f"PERF-{o:04d}",
            order_type="dine_in",
            channel="pos",
            status="open" if o % 5 else "completed",
            payment_status="unpaid",
            subtotal=Decimal("200"),
            tax=Decimal("10"),
            total=Decimal("210"),
            placed_at=timezone.now(),
            created_by=owner,
        )
        oi = OrderItem.objects.create(
            order=order, name="Perf Item", quantity=1, unit_price=Decimal("200"), station="Hot"
        )
        OrderItemModifier.objects.create(item=oi, name="Large", price=Decimal("30"))

    # Staff, customers, todos, audit
    for i in range(25):
        StaffProfile.objects.create(
            restaurant=restaurant, name=f"Staff {i}", phone=f"98000000{i:02d}", wage_amount=Decimal("500")
        )
    for i in range(40):
        cust = RestaurantCustomer.objects.create(
            restaurant=restaurant, name=f"Cust {i}", phone=f"97000000{i:02d}"
        )
        CustomerAddress.objects.create(customer=cust, label="Home", line=f"Addr {i}", is_default=True)
    for i in range(30):
        Todo.objects.create(restaurant=restaurant, title=f"Todo {i}", priority="normal", created_by=owner)
    for i in range(50):
        AuditLog.objects.create(
            restaurant=restaurant, actor=owner, action=f"perf.action.{i}", object_type="perf", object_id=str(i)
        )

    # Journals for accounting report N+1
    today = timezone.localdate()
    for i in range(15):
        create_journal(
            restaurant=restaurant,
            entry_date=today,
            lines=[
                {"account_code": "1010", "debit": 100, "credit": 0},
                {"account_code": "4100", "debit": 0, "credit": 100},
            ],
            memo=f"perf sale {i}",
            reference=f"perf:sale:{i}",
            user=owner,
        )

    client = APIClient()
    login = client.post("/api/v1/auth/login/", {"identifier": email, "password": "demo1234"}, format="json")
    assert login.status_code == 200, login.content
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_RESTAURANT_ID=str(restaurant.id),
    )
    return {"restaurant": restaurant, "owner": owner, "client": client}


# Endpoints measured for list/report GET performance (relative to /api/v1)
PERF_GET_ENDPOINTS = [
    "/menu/categories/",
    "/menu/items/",
    "/orders/",
    "/tables/",
    "/kitchen/",
    "/inventory/items/",
    "/inventory/suppliers/",
    "/inventory/purchases/",
    "/inventory/movements/",
    "/production/recipes/",
    "/production/batches/",
    "/staff/employees/",
    "/staff/attendance/",
    "/staff/wages/",
    "/staff/payroll/",
    "/expenses/",
    "/expenses/categories/",
    "/customers/",
    "/todos/",
    "/accounting/accounts/",
    "/accounting/journals/",
    "/accounting/trial-balance/",
    "/accounting/profit-and-loss/?from=2020-01-01&to=2030-12-31",
    "/accounting/balance-sheet/",
    "/accounting/gst/?from=2020-01-01&to=2030-12-31",
    "/loyalty/programs/",
    "/loyalty/rules/",
    "/loyalty/rewards/",
    "/loyalty/ledger/",
    "/delivery/agents/",
    "/delivery/jobs/",
    "/notifications/",
    "/audit/",
    "/reports/overview/",
]
