"""Senior QA scenarios for recent wastage, credit purchase, and recipe-cost changes."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.billing.services import ensure_catalog_and_roles, provision_restaurant

pytestmark = pytest.mark.django_db


def _user(email: str):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    u, created = User.objects.get_or_create(email=email, defaults={"display_name": email.split("@")[0]})
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
def qa_ctx():
    ensure_catalog_and_roles()
    from apps.restaurants.models import Permission, RestaurantMembership, Role

    owner = _user("qa2.owner@test.com")
    kitchen = _user("qa2.kitchen@test.com")
    acct = _user("qa2.acct@test.com")
    restaurant = provision_restaurant(owner=owner, name="QA Logic Lab 2")
    kitchen_role = Role.objects.get(code="kitchen")
    kitchen_role.permissions.add(Permission.objects.get(scope="recipes", action="Edit"))
    RestaurantMembership.objects.get_or_create(
        user=kitchen, restaurant=restaurant, defaults={"role": kitchen_role}
    )
    RestaurantMembership.objects.get_or_create(
        user=acct, restaurant=restaurant, defaults={"role": Role.objects.get(code="accountant")}
    )
    from apps.inventory.models import Unit

    kg, _ = Unit.objects.get_or_create(code="qa2_kg", defaults={"name": "Kg", "kind": "mass", "to_base": Decimal("1")})
    litre, _ = Unit.objects.get_or_create(
        code="qa2_l", defaults={"name": "Litre", "kind": "volume", "to_base": Decimal("1")}
    )
    return {
        "restaurant": restaurant,
        "owner_c": _login("qa2.owner@test.com", restaurant.id),
        "kitchen_c": _login("qa2.kitchen@test.com", restaurant.id),
        "acct_c": _login("qa2.acct@test.com", restaurant.id),
        "kg": kg,
        "litre": litre,
    }


def test_qa_ingredient_wastage_and_scaling(qa_ctx):
    from apps.inventory.models import InventoryItem, InventoryLedger
    from apps.production.models import ProductionBatch, Recipe, RecipeIngredient
    from apps.production.services import complete_batch

    r = qa_ctx["restaurant"]
    melon = InventoryItem.objects.create(
        restaurant=r, name="QA2 Melon", unit=qa_ctx["kg"], quantity=Decimal("20"), cost=Decimal("30")
    )
    recipe = Recipe.objects.create(restaurant=r, name="QA2 Juice", yield_qty=Decimal("4"), yield_unit="qa2_l")
    RecipeIngredient.objects.create(
        recipe=recipe,
        inventory_item=melon,
        quantity=Decimal("5"),
        unit_code="qa2_kg",
        wastage_qty=Decimal("0.5"),
        wastage_note="shell",
    )
    batch = ProductionBatch.objects.create(
        restaurant=r, recipe=recipe, product_name="QA2 Juice", unit="qa2_l", planned_qty=4, actual_qty=4
    )
    complete_batch(batch, actual=4)
    melon.refresh_from_db()
    assert melon.quantity == Decimal("15")
    waste = InventoryLedger.objects.filter(item=melon, entry_type=InventoryLedger.Type.WASTE).latest("created_at")
    use = InventoryLedger.objects.filter(
        item=melon, entry_type=InventoryLedger.Type.PRODUCTION_CONSUMPTION
    ).latest("created_at")
    assert waste.quantity == Decimal("-0.5")
    assert use.quantity == Decimal("-4.5")
    assert "shell" in (waste.note or "")

    melon.quantity = Decimal("30")
    melon.save()
    batch2 = ProductionBatch.objects.create(
        restaurant=r, recipe=recipe, product_name="QA2 Juice", unit="qa2_l", planned_qty=8, actual_qty=8
    )
    complete_batch(batch2, actual=8)
    melon.refresh_from_db()
    assert melon.quantity == Decimal("20")  # 30 - 10
    w2 = InventoryLedger.objects.filter(item=melon, entry_type=InventoryLedger.Type.WASTE, reference=str(batch2.id)).first()
    assert w2 is not None and w2.quantity == Decimal("-1.0")


def test_qa_balance_leftover_wastage(qa_ctx):
    from apps.inventory.models import InventoryItem
    from apps.production.models import ProductionBatch, Recipe, RecipeIngredient
    from apps.production.services import complete_batch, record_balance_waste

    r = qa_ctx["restaurant"]
    melon = InventoryItem.objects.create(
        restaurant=r, name="QA2 Melon B", unit=qa_ctx["kg"], quantity=Decimal("50"), cost=Decimal("30")
    )
    juice = InventoryItem.objects.create(
        restaurant=r, name="QA2 Juice FG", unit=qa_ctx["litre"], quantity=Decimal("0")
    )
    recipe = Recipe.objects.create(
        restaurant=r, name="QA2 Juice B", yield_qty=Decimal("4"), yield_unit="qa2_l", output_item=juice
    )
    RecipeIngredient.objects.create(
        recipe=recipe, inventory_item=melon, quantity=Decimal("5"), unit_code="qa2_kg", wastage_qty=Decimal("0.5")
    )
    batch = ProductionBatch.objects.create(
        restaurant=r, recipe=recipe, product_name="QA2 Juice B", unit="qa2_l", planned_qty=4, actual_qty=4
    )
    complete_batch(batch, actual=4, wastage=1)
    juice.refresh_from_db()
    assert juice.quantity == Decimal("3")
    assert batch.wastage_qty == Decimal("1")

    record_balance_waste(batch, quantity=Decimal("0.5"), note="EOD")
    juice.refresh_from_db()
    batch.refresh_from_db()
    assert juice.quantity == Decimal("2.5")
    assert batch.wastage_qty == Decimal("1.5")

    planned = ProductionBatch.objects.create(
        restaurant=r,
        recipe=recipe,
        product_name="QA2 Juice B",
        unit="qa2_l",
        planned_qty=2,
        status=ProductionBatch.Status.PLANNED,
    )
    with pytest.raises(ValueError):
        record_balance_waste(planned, quantity=Decimal("1"))

    # leftover > produced clamps
    before = juice.quantity
    over = ProductionBatch.objects.create(
        restaurant=r, recipe=recipe, product_name="QA2 Juice B", unit="qa2_l", planned_qty=2, actual_qty=2
    )
    complete_batch(over, actual=2, wastage=99)
    juice.refresh_from_db()
    over.refresh_from_db()
    assert over.wastage_qty == Decimal("2")
    assert juice.quantity == before  # +2 in -2 waste

    # idempotent complete
    complete_batch(over, actual=2, wastage=1)
    juice.refresh_from_db()
    assert juice.quantity == before

    # API
    res = qa_ctx["owner_c"].post(
        f"/api/v1/production/batches/{batch.id}/balance-waste/",
        {"quantity": "0.25", "note": "API"},
        format="json",
    )
    assert res.status_code == 200
    assert Decimal(str(res.data["wastage_qty"])) == Decimal("1.75")


def test_qa_credit_purchase_settlement_and_cost_history(qa_ctx):
    from apps.inventory.models import InventoryItem, InventoryLedger, PurchaseOrder, Supplier

    r = qa_ctx["restaurant"]
    owner_c = qa_ctx["owner_c"]
    supplier = Supplier.objects.create(restaurant=r, name="QA2 Traders", payment_terms="Net 15")
    sugar = InventoryItem.objects.create(
        restaurant=r, name="QA2 Sugar", unit=qa_ctx["kg"], quantity=Decimal("10"), cost=Decimal("90")
    )

    create = owner_c.post(
        "/api/v1/inventory/purchases/",
        {
            "supplier": str(supplier.id),
            "is_credit": True,
            "tax_rate": "0",
            "status": "ordered",
            "lines": [{"item": str(sugar.id), "quantity": "5", "unit_cost": "100"}],
        },
        format="json",
    )
    assert create.status_code in (200, 201), create.data
    assert Decimal(str(create.data["total"])) == Decimal("500.00")
    assert create.data["payment_status"] == "unpaid"
    assert Decimal(str(create.data["pending_amount"])) == Decimal("500.00")
    po_id = create.data["id"]

    recv = owner_c.post(f"/api/v1/inventory/purchases/{po_id}/receive/", {}, format="json")
    assert recv.status_code == 200, recv.data
    sugar.refresh_from_db()
    assert sugar.quantity == Decimal("15")
    assert sugar.cost == Decimal("100")
    led = InventoryLedger.objects.filter(item=sugar, entry_type=InventoryLedger.Type.PURCHASE).latest("created_at")
    assert led.unit_cost == Decimal("100")
    assert led.amount == Decimal("500.00")

    again = owner_c.post(f"/api/v1/inventory/purchases/{po_id}/receive/", {}, format="json")
    assert again.status_code == 400

    s1 = owner_c.post(
        f"/api/v1/inventory/purchases/{po_id}/settle/",
        {"amount": "200", "method": "upi"},
        format="json",
    )
    assert s1.status_code == 200
    assert s1.data["payment_status"] == "partial"
    assert Decimal(str(s1.data["pending_amount"])) == Decimal("300.00")

    s2 = owner_c.post(
        f"/api/v1/inventory/purchases/{po_id}/settle/",
        {"amount": "9999", "method": "cash"},
        format="json",
    )
    assert s2.status_code == 200
    assert s2.data["payment_status"] == "paid"
    assert Decimal(str(s2.data["pending_amount"])) == Decimal("0")

    # second price for history
    create2 = owner_c.post(
        "/api/v1/inventory/purchases/",
        {
            "supplier": str(supplier.id),
            "is_credit": True,
            "tax_rate": "0",
            "lines": [{"item": str(sugar.id), "quantity": "2", "unit_cost": "110"}],
        },
        format="json",
    )
    assert create2.status_code in (200, 201)
    owner_c.post(f"/api/v1/inventory/purchases/{create2.data['id']}/receive/", {}, format="json")
    sugar.refresh_from_db()
    assert sugar.cost == Decimal("110")

    hist = owner_c.get(f"/api/v1/inventory/items/{sugar.id}/cost-history/")
    assert hist.status_code == 200
    costs = {Decimal(str(row["unit_cost"])) for row in hist.data}
    assert Decimal("100") in costs and Decimal("110") in costs

    cash = owner_c.post(
        "/api/v1/inventory/purchases/",
        {
            "supplier": str(supplier.id),
            "is_credit": False,
            "tax_rate": "0",
            "lines": [{"item": str(sugar.id), "quantity": "1", "unit_cost": "105"}],
        },
        format="json",
    )
    cr = owner_c.post(f"/api/v1/inventory/purchases/{cash.data['id']}/receive/", {}, format="json")
    assert cr.status_code == 200
    assert cr.data["payment_status"] == "paid"

    tax = owner_c.post(
        "/api/v1/inventory/purchases/",
        {
            "supplier": str(supplier.id),
            "is_credit": True,
            "tax_rate": "5",
            "lines": [{"item": str(sugar.id), "quantity": "10", "unit_cost": "100"}],
        },
        format="json",
    )
    assert tax.status_code in (200, 201)
    assert Decimal(str(tax.data["total"])) == Decimal("1050.00")

    pay = owner_c.get("/api/v1/inventory/purchases/payables/")
    assert pay.status_code == 200
    pending_ids = {row["id"] for row in pay.data["results"]}
    assert create2.data["id"] in pending_ids
    assert tax.data["id"] in pending_ids

    suppliers = owner_c.get("/api/v1/inventory/suppliers/")
    rows = suppliers.data if isinstance(suppliers.data, list) else suppliers.data.get("results", [])
    row = next(x for x in rows if str(x["id"]) == str(supplier.id))
    # open: 2*110=220 + 1050 = 1270
    assert Decimal(str(row["outstanding"])) == Decimal("1270.00")

    zero = owner_c.post(
        f"/api/v1/inventory/purchases/{create2.data['id']}/settle/",
        {"amount": "0"},
        format="json",
    )
    assert zero.status_code == 400

    from apps.inventory.services import settle_purchase

    paid = PurchaseOrder.objects.get(id=po_id)
    with pytest.raises(ValueError):
        settle_purchase(paid, amount=Decimal("10"))


def test_qa_recipe_costs_permissions_and_math(qa_ctx):
    from apps.inventory.models import InventoryItem
    from apps.production.models import Recipe, RecipeIngredient

    r = qa_ctx["restaurant"]
    melon = InventoryItem.objects.create(
        restaurant=r, name="QA2 Melon C", unit=qa_ctx["kg"], quantity=Decimal("20"), cost=Decimal("30")
    )
    recipe = Recipe.objects.create(
        restaurant=r,
        name="QA2 Costed Juice",
        yield_qty=Decimal("4"),
        electricity_cost=Decimal("10"),
        gas_fuel_cost=Decimal("5"),
        labor_cost=Decimal("25"),
        selling_price=Decimal("300"),
    )
    RecipeIngredient.objects.create(
        recipe=recipe, inventory_item=melon, quantity=Decimal("5"), unit_code="qa2_kg"
    )

    assert recipe.ingredient_cost() == Decimal("150.00")
    assert recipe.overhead_cost() == Decimal("40")
    assert recipe.total_cost() == Decimal("190.00")
    assert recipe.profit_amount() == Decimal("110.00")

    owner = qa_ctx["owner_c"].get(f"/api/v1/production/recipes/{recipe.id}/")
    assert owner.status_code == 200
    assert owner.data["can_view_costs"] is True
    assert owner.data["can_edit_costs"] is True
    assert Decimal(str(owner.data["ingredient_cost"])) == Decimal("150.00")
    assert Decimal(str(owner.data["overhead_cost"])) == Decimal("40.00")
    assert Decimal(str(owner.data["total_cost"])) == Decimal("190.00")
    assert Decimal(str(owner.data["profit_amount"])) == Decimal("110.00")
    assert Decimal(str(owner.data["cost_per_unit"])) == Decimal("47.50")

    kitchen = qa_ctx["kitchen_c"].get(f"/api/v1/production/recipes/{recipe.id}/")
    assert kitchen.status_code == 200
    assert kitchen.data["can_view_costs"] is False
    assert kitchen.data["labor_cost"] is None
    assert kitchen.data["total_cost"] is None
    assert kitchen.data["ingredients"][0]["estimated_cost"] is None

    denied = qa_ctx["kitchen_c"].patch(
        f"/api/v1/production/recipes/{recipe.id}/",
        {"labor_cost": "999"},
        format="json",
    )
    assert denied.status_code == 400

    allowed = qa_ctx["kitchen_c"].patch(
        f"/api/v1/production/recipes/{recipe.id}/",
        {"station": "Beverages-QA"},
        format="json",
    )
    assert allowed.status_code == 200
    assert allowed.data["station"] == "Beverages-QA"

    acct = qa_ctx["acct_c"].get(f"/api/v1/production/recipes/{recipe.id}/")
    assert acct.status_code == 200
    assert acct.data["can_view_costs"] is True
    assert acct.data["can_edit_costs"] is False
    assert Decimal(str(acct.data["total_cost"])) == Decimal("190.00")

    acct_edit = qa_ctx["acct_c"].patch(
        f"/api/v1/production/recipes/{recipe.id}/",
        {"electricity_cost": "1"},
        format="json",
    )
    assert acct_edit.status_code in (400, 403)

    updated = qa_ctx["owner_c"].patch(
        f"/api/v1/production/recipes/{recipe.id}/",
        {"electricity_cost": "20", "selling_price": "350"},
        format="json",
    )
    assert updated.status_code == 200
    assert Decimal(str(updated.data["electricity_cost"])) == Decimal("20.00")
    # overhead 20+5+25=50; total 200; profit 150
    assert Decimal(str(updated.data["profit_amount"])) == Decimal("150.00")


def test_qa_modules_get_allowed_for_accountant_post_requires_edit(qa_ctx):
    """GET /restaurants/current/modules/ must not require subscription Edit."""
    get_res = qa_ctx["acct_c"].get("/api/v1/restaurants/current/modules/")
    assert get_res.status_code == 200, get_res.content
    assert "enabled" in get_res.data
    assert "catalog" in get_res.data

    post_res = qa_ctx["acct_c"].post(
        "/api/v1/restaurants/current/modules/",
        {"modules": get_res.data.get("enabled") or []},
        format="json",
    )
    assert post_res.status_code in (403, 400)
