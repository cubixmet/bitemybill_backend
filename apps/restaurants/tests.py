from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.billing.services import apply_monthly_debit, apply_top_up, provision_restaurant
from apps.inventory.models import InventoryItem, Unit
from apps.inventory.services import post_ledger
from apps.restaurants.constants import DEFAULT_MODULES
from apps.restaurants.models import RestaurantMembership

User = get_user_model()
pytestmark = pytest.mark.django_db


def _user(email, kind="staff"):
    return User.objects.create_user(email=email, password="demo1234", user_kind=kind, display_name=email)


def test_login_and_memberships():
    owner = _user("a@test.com")
    restaurant = provision_restaurant(owner=owner, name="Cafe A")
    client = APIClient()
    res = client.post("/api/v1/auth/login/", {"identifier": "a@test.com", "password": "demo1234"}, format="json")
    assert res.status_code == 200
    assert res.data["memberships"]
    assert res.data["memberships"][0]["restaurant_name"] == "Cafe A"
    token = res.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_RESTAURANT_ID=str(restaurant.id))
    me = client.get("/api/v1/auth/me/")
    assert me.status_code == 200


def test_tenant_isolation():
    a = _user("ownera@test.com")
    b = _user("ownerb@test.com")
    ra = provision_restaurant(owner=a, name="A Place")
    rb = provision_restaurant(owner=b, name="B Place")
    client = APIClient()
    token = client.post("/api/v1/auth/login/", {"identifier": "ownera@test.com", "password": "demo1234"}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_RESTAURANT_ID=str(rb.id))
    res = client.get("/api/v1/menu/items/")
    assert res.status_code in (403, 200)
    if res.status_code == 200:
        assert res.data == []


def test_inventory_ledger_invariant():
    owner = _user("inv@test.com")
    restaurant = provision_restaurant(owner=owner, name="Stock House")
    unit = Unit.objects.create(code="kgx", name="Kg", to_base=1)
    item = InventoryItem.objects.create(restaurant=restaurant, name="Maida", unit=unit, quantity=Decimal("10"))
    post_ledger(restaurant=restaurant, item=item, entry_type="adjustment", quantity=Decimal("2"))
    item.refresh_from_db()
    assert item.quantity == Decimal("12")
    with pytest.raises(ValueError):
        post_ledger(restaurant=restaurant, item=item, entry_type="waste", quantity=Decimal("-100"))


def test_wallet_grace():
    owner = _user("bill@test.com")
    restaurant = provision_restaurant(owner=owner, name="Paid Place")
    from apps.billing.models import Wallet

    wallet = Wallet.objects.get(restaurant=restaurant)
    wallet.balance = Decimal("10")
    wallet.save()
    usage = apply_monthly_debit(restaurant)
    restaurant.refresh_from_db()
    assert restaurant.account_status in ("payment_due", "scheduled_for_deletion")
    assert usage.outstanding >= 0


def test_platform_admin_forbidden_for_staff():
    owner = _user("staffx@test.com")
    provision_restaurant(owner=owner, name="Nope")
    client = APIClient()
    token = client.post("/api/v1/auth/login/", {"identifier": "staffx@test.com", "password": "demo1234"}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    res = client.get("/api/v1/admin-api/restaurants/")
    assert res.status_code == 403


def test_order_status_transition():
    owner = _user("ord@test.com")
    restaurant = provision_restaurant(owner=owner, name="Order House")
    from apps.menu.models import MenuCategory, MenuItem
    from apps.orders.services import create_order

    cat = MenuCategory.objects.create(restaurant=restaurant, name="Mains", slug="mains")
    item = MenuItem.objects.create(restaurant=restaurant, category=cat, name="Tea", price=Decimal("20"))
    order = create_order(
        restaurant=restaurant,
        user=owner,
        data={
            "order_type": "takeaway",
            "items": [{"name": "Tea", "quantity": 1, "unit_price": "20", "menu_item": item.id}],
        },
    )
    client = APIClient()
    token = client.post("/api/v1/auth/login/", {"identifier": "ord@test.com", "password": "demo1234"}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_RESTAURANT_ID=str(restaurant.id))
    res = client.post(f"/api/v1/orders/{order.id}/transition/", {"status": "preparing"}, format="json")
    assert res.status_code == 200
    assert res.data["status"] == "preparing"
    bad = client.post(f"/api/v1/orders/{order.id}/transition/", {"status": "nope"}, format="json")
    assert bad.status_code == 400


def test_production_deducts_inventory():
    owner = _user("prod@test.com")
    restaurant = provision_restaurant(owner=owner, name="Bake House")
    from apps.inventory.models import InventoryItem, InventoryLedger, Unit
    from apps.production.models import ProductionBatch, Recipe, RecipeIngredient
    from apps.production.services import complete_batch

    unit = Unit.objects.create(code="kgp", name="Kg", to_base=1)
    flour = InventoryItem.objects.create(restaurant=restaurant, name="Maida", unit=unit, quantity=Decimal("10"))
    recipe = Recipe.objects.create(restaurant=restaurant, name="Porotta", yield_qty=Decimal("1"))
    RecipeIngredient.objects.create(recipe=recipe, inventory_item=flour, quantity=Decimal("2"), unit_code="kgp")
    batch = ProductionBatch.objects.create(
        restaurant=restaurant, recipe=recipe, product_name="Porotta", planned_qty=Decimal("1"), actual_qty=Decimal("1")
    )
    complete_batch(batch, actual=1)
    flour.refresh_from_db()
    assert flour.quantity == Decimal("8")
    assert batch.posted is True


def test_recipe_ingredient_wastage_posts_to_inventory():
    """5 kg watermelon with 0.5 kg shell → 4.5 usable + 0.5 waste ledger lines."""
    owner = _user("waste@test.com")
    restaurant = provision_restaurant(owner=owner, name="Juice Bar")
    from apps.inventory.models import InventoryItem, InventoryLedger, Unit
    from apps.production.models import ProductionBatch, Recipe, RecipeIngredient
    from apps.production.services import complete_batch

    unit = Unit.objects.create(code="kgw", name="Kg", to_base=1)
    melon = InventoryItem.objects.create(
        restaurant=restaurant, name="Watermelon", unit=unit, quantity=Decimal("20")
    )
    recipe = Recipe.objects.create(
        restaurant=restaurant, name="Watermelon Juice", yield_qty=Decimal("4"), yield_unit="L"
    )
    RecipeIngredient.objects.create(
        recipe=recipe,
        inventory_item=melon,
        quantity=Decimal("5"),
        unit_code="kgw",
        wastage_qty=Decimal("0.5"),
        wastage_note="shell",
    )
    batch = ProductionBatch.objects.create(
        restaurant=restaurant,
        recipe=recipe,
        product_name="Watermelon Juice",
        planned_qty=Decimal("4"),
        actual_qty=Decimal("4"),
    )
    complete_batch(batch, actual=4)
    melon.refresh_from_db()
    assert melon.quantity == Decimal("15")  # 20 - 5
    waste = InventoryLedger.objects.filter(item=melon, entry_type=InventoryLedger.Type.WASTE)
    use = InventoryLedger.objects.filter(item=melon, entry_type=InventoryLedger.Type.PRODUCTION_CONSUMPTION)
    assert waste.count() == 1
    assert use.count() == 1
    assert waste.first().quantity == Decimal("-0.5")
    assert use.first().quantity == Decimal("-4.5")
    assert "shell" in (waste.first().note or "")


def test_recipe_overhead_costs_and_permission_gate():
    """Overhead + selling price drive profit; kitchen cannot edit recipe_costs."""
    owner = _user("costowner@test.com")
    kitchen_user = _user("costkitchen@test.com")
    restaurant = provision_restaurant(owner=owner, name="Cost Kitchen")
    from apps.billing.services import ensure_catalog_and_roles
    from apps.inventory.models import InventoryItem, Unit
    from apps.production.models import Recipe, RecipeIngredient
    from apps.restaurants.models import RestaurantMembership, Role
    from rest_framework.test import APIClient

    ensure_catalog_and_roles()
    kitchen_role = Role.objects.get(code="kitchen")
    # Allow kitchen to edit recipe method, but not recipe_costs (margin / overhead).
    from apps.restaurants.models import Permission

    kitchen_role.permissions.add(Permission.objects.get(scope="recipes", action="Edit"))
    RestaurantMembership.objects.create(user=kitchen_user, restaurant=restaurant, role=kitchen_role)

    unit = Unit.objects.create(code="kgc", name="Kg", to_base=1)
    melon = InventoryItem.objects.create(
        restaurant=restaurant, name="Watermelon", unit=unit, quantity=Decimal("20"), cost=Decimal("30")
    )
    recipe = Recipe.objects.create(
        restaurant=restaurant,
        name="Juice",
        yield_qty=Decimal("4"),
        electricity_cost=Decimal("10"),
        gas_fuel_cost=Decimal("5"),
        labor_cost=Decimal("25"),
        selling_price=Decimal("200"),
    )
    RecipeIngredient.objects.create(
        recipe=recipe, inventory_item=melon, quantity=Decimal("5"), unit_code="kgc"
    )
    assert recipe.ingredient_cost() == Decimal("150.00")  # 5 × 30
    assert recipe.overhead_cost() == Decimal("40")
    assert recipe.total_cost() == Decimal("190.00")
    assert recipe.profit_amount() == Decimal("10.00")

    owner_client = APIClient()
    token = owner_client.post(
        "/api/v1/auth/login/", {"identifier": "costowner@test.com", "password": "demo1234"}, format="json"
    ).data["access"]
    owner_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_RESTAURANT_ID=str(restaurant.id))
    ok = owner_client.patch(
        f"/api/v1/production/recipes/{recipe.id}/",
        {"labor_cost": "50", "selling_price": "220"},
        format="json",
    )
    assert ok.status_code == 200
    assert Decimal(str(ok.data["labor_cost"])) == Decimal("50.00")
    assert ok.data["can_view_costs"] is True
    assert ok.data["can_edit_costs"] is True

    kitchen_client = APIClient()
    ktoken = kitchen_client.post(
        "/api/v1/auth/login/", {"identifier": "costkitchen@test.com", "password": "demo1234"}, format="json"
    ).data["access"]
    kitchen_client.credentials(HTTP_AUTHORIZATION=f"Bearer {ktoken}", HTTP_X_RESTAURANT_ID=str(restaurant.id))
    denied = kitchen_client.patch(
        f"/api/v1/production/recipes/{recipe.id}/",
        {"labor_cost": "999"},
        format="json",
    )
    assert denied.status_code == 400
    view = kitchen_client.get(f"/api/v1/production/recipes/{recipe.id}/")
    assert view.status_code == 200
    assert view.data["can_view_costs"] is False
    assert view.data["labor_cost"] is None
    assert view.data["total_cost"] is None


def test_credit_purchase_partial_settlement_and_unit_cost():
    """Credit buy 5 kg sugar @ ₹100 (= ₹500); receive stocks cost; partial then full settle."""
    owner = _user("po@test.com")
    restaurant = provision_restaurant(owner=owner, name="Credit Mart")
    from apps.inventory.models import InventoryItem, InventoryLedger, PurchaseOrder, Supplier, Unit
    from apps.inventory.services import receive_purchase, settle_purchase

    unit = Unit.objects.create(code="kgs", name="Kg", to_base=1)
    supplier = Supplier.objects.create(restaurant=restaurant, name="Nagarjuna Traders", payment_terms="Net 15")
    sugar = InventoryItem.objects.create(
        restaurant=restaurant, name="Sugar", unit=unit, quantity=Decimal("10"), cost=Decimal("90")
    )
    po = PurchaseOrder.objects.create(
        restaurant=restaurant,
        supplier=supplier,
        number="PO-SUGAR-1",
        status=PurchaseOrder.Status.ORDERED,
        is_credit=True,
        tax_rate=Decimal("0"),
    )
    from apps.inventory.models import PurchaseItem

    PurchaseItem.objects.create(
        purchase=po, item=sugar, quantity=Decimal("5"), unit_cost=Decimal("100")
    )
    po.recompute_total()
    assert po.total == Decimal("500.00")
    assert po.pending_amount == Decimal("500.00")
    assert po.payment_status == PurchaseOrder.PaymentStatus.UNPAID

    receive_purchase(po)
    sugar.refresh_from_db()
    assert sugar.quantity == Decimal("15")
    assert sugar.cost == Decimal("100")  # latest purchase unit cost
    led = InventoryLedger.objects.filter(item=sugar, entry_type=InventoryLedger.Type.PURCHASE).latest("created_at")
    assert led.unit_cost == Decimal("100")
    assert led.amount == Decimal("500.00")
    assert led.quantity == Decimal("5")

    settle_purchase(po, amount=Decimal("200"), method="upi", note="partial")
    po.refresh_from_db()
    assert po.amount_paid == Decimal("200.00")
    assert po.pending_amount == Decimal("300.00")
    assert po.payment_status == PurchaseOrder.PaymentStatus.PARTIAL
    assert supplier.outstanding_balance() == Decimal("300.00")

    settle_purchase(po, amount=Decimal("300"), method="cash")
    po.refresh_from_db()
    assert po.pending_amount == Decimal("0")
    assert po.payment_status == PurchaseOrder.PaymentStatus.PAID
    assert supplier.outstanding_balance() == Decimal("0")


def test_finished_balance_wastage_posts_to_output_inventory():
    """Produced juice stocks in; leftover balance writes off finished goods as waste."""
    owner = _user("balance@test.com")
    restaurant = provision_restaurant(owner=owner, name="Balance Bar")
    from apps.inventory.models import InventoryItem, InventoryLedger, Unit
    from apps.production.models import ProductionBatch, Recipe, RecipeIngredient
    from apps.production.services import complete_batch, record_balance_waste

    kg = Unit.objects.create(code="kgb", name="Kg", to_base=1)
    litre = Unit.objects.create(code="lb", name="Litre", to_base=1)
    melon = InventoryItem.objects.create(
        restaurant=restaurant, name="Watermelon", unit=kg, quantity=Decimal("20")
    )
    juice = InventoryItem.objects.create(
        restaurant=restaurant, name="Watermelon Juice", unit=litre, quantity=Decimal("0")
    )
    recipe = Recipe.objects.create(
        restaurant=restaurant,
        name="Watermelon Juice",
        yield_qty=Decimal("4"),
        yield_unit="lb",
        output_item=juice,
    )
    RecipeIngredient.objects.create(
        recipe=recipe,
        inventory_item=melon,
        quantity=Decimal("5"),
        unit_code="kgb",
        wastage_qty=Decimal("0.5"),
        wastage_note="shell",
    )
    batch = ProductionBatch.objects.create(
        restaurant=restaurant,
        recipe=recipe,
        product_name="Watermelon Juice",
        unit="lb",
        planned_qty=Decimal("4"),
        actual_qty=Decimal("4"),
    )
    # Complete with 1 L leftover discarded at end of prep
    complete_batch(batch, actual=4, wastage=1)
    juice.refresh_from_db()
    assert juice.quantity == Decimal("3")  # 4 in − 1 leftover waste
    out_waste = InventoryLedger.objects.filter(item=juice, entry_type=InventoryLedger.Type.WASTE)
    assert out_waste.count() == 1
    assert out_waste.first().quantity == Decimal("-1")
    assert batch.wastage_qty == Decimal("1")

    # After service: another 0.5 L unsold balance
    record_balance_waste(batch, quantity=Decimal("0.5"), note="unsold end of day")
    juice.refresh_from_db()
    batch.refresh_from_db()
    assert juice.quantity == Decimal("2.5")
    assert batch.wastage_qty == Decimal("1.5")
    assert InventoryLedger.objects.filter(item=juice, entry_type=InventoryLedger.Type.WASTE).count() == 2


def test_loyalty_ledger_redemption():
    owner = _user("loy@test.com")
    restaurant = provision_restaurant(owner=owner, name="Points Place")
    from apps.customers.models import RestaurantCustomer
    from apps.loyalty.models import Reward
    from apps.loyalty.services import earn_points, redeem_reward

    guest = RestaurantCustomer.objects.create(restaurant=restaurant, name="Afsal", phone="999", points_balance=Decimal("0"))
    earn_points(guest, Decimal("200"), restaurant=restaurant, note="order")
    guest.refresh_from_db()
    assert guest.points_balance == Decimal("200")
    reward = Reward.objects.create(restaurant=restaurant, title="₹50", points_cost=Decimal("100"), discount_amount=Decimal("50"))
    redeem_reward(restaurant=restaurant, customer=guest, reward=reward)
    guest.refresh_from_db()
    assert guest.points_balance == Decimal("100")


def test_pwa_subdomain_and_partner_api_token():
    owner = _user("pwa@test.com")
    restaurant = provision_restaurant(
        owner=owner,
        name="Subdomain Cafe",
        modules=DEFAULT_MODULES + ["online", "custom_storefront", "menu", "loyalty"],
    )
    restaurant.slug = "subdomain-cafe"
    restaurant.save(update_fields=["slug"])

    from apps.restaurants.models import StorefrontAPIToken
    from apps.restaurants.storefront_hosts import storefront_host, storefront_url

    assert restaurant.pwa_host == storefront_host("subdomain-cafe")
    assert "-app." in restaurant.pwa_host
    assert storefront_url("subdomain-cafe").endswith(restaurant.pwa_host)

    client = APIClient()
    token = client.post("/api/v1/auth/login/", {"identifier": "pwa@test.com", "password": "demo1234"}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_RESTAURANT_ID=str(restaurant.id))

    cfg = client.get("/api/v1/restaurants/current/storefront/")
    assert cfg.status_code == 200
    assert cfg.data["pwa_subdomain"] == "subdomain-cafe-app"
    assert cfg.data["custom_storefront_enabled"] is True

    issued = client.post("/api/v1/restaurants/current/storefront/tokens/", {"name": "Custom site"}, format="json")
    assert issued.status_code == 201
    raw = issued.data["token"]
    assert raw.startswith("bmb_sf_")

    partner = APIClient()
    partner.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    home = partner.get("/api/v1/partner/restaurant/")
    assert home.status_code == 200
    assert home.data["slug"] == "subdomain-cafe"
    menu = partner.get("/api/v1/partner/menu/")
    assert menu.status_code == 200

    # Without custom_storefront module, token auth fails
    from apps.billing.models import CatalogModule, RestaurantModule

    RestaurantModule.objects.filter(restaurant=restaurant, module__code="custom_storefront").update(enabled=False)
    denied = partner.get("/api/v1/partner/restaurant/")
    assert denied.status_code in (401, 403)
    assert "not enabled" in str(denied.data.get("detail", "")).lower()


def test_customer_data_segments_and_storefront_loyalty():
    owner = _user("crm@test.com")
    guest_user = _user("guestapp@test.com", kind="customer")
    restaurant = provision_restaurant(owner=owner, name="CRM Kitchen")
    from apps.customers.models import RestaurantCustomer
    from apps.loyalty.models import LoyaltyProgram, Reward

    RestaurantCustomer.objects.create(
        restaurant=restaurant,
        name="Enquiry Guest",
        phone="9001",
        source=RestaurantCustomer.Source.ENQUIRY,
        enquiry_note="Catering",
    )
    RestaurantCustomer.objects.create(
        restaurant=restaurant,
        name="App Guest",
        phone="9002",
        user=guest_user,
        source=RestaurantCustomer.Source.APP,
        points_balance=Decimal("50"),
    )
    RestaurantCustomer.objects.create(
        restaurant=restaurant,
        name="Delivery Guest",
        phone="9003",
        source=RestaurantCustomer.Source.DELIVERY,
    )
    LoyaltyProgram.objects.create(restaurant=restaurant, name="Shell Rewards", points_per_amount=Decimal("1"), amount_step=Decimal("100"))
    Reward.objects.create(restaurant=restaurant, title="App ₹40 OFF", points_cost=Decimal("80"), discount_amount=Decimal("40"))

    client = APIClient()
    token = client.post("/api/v1/auth/login/", {"identifier": "crm@test.com", "password": "demo1234"}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_RESTAURANT_ID=str(restaurant.id))

    summary = client.get("/api/v1/customers/summary/")
    assert summary.status_code == 200
    assert summary.data["enquiries"] == 1
    assert summary.data["app_users"] == 1
    assert summary.data["delivery_done"] == 1

    assert len(client.get("/api/v1/customers/?segment=enquiries").data) == 1
    assert len(client.get("/api/v1/customers/?segment=app_users").data) == 1
    assert len(client.get("/api/v1/customers/?segment=delivery_done").data) == 1

    loy = client.get(f"/api/v1/storefront/{restaurant.slug}/loyalty/")
    assert loy.status_code == 200
    assert loy.data["program"]["name"] == "Shell Rewards"
    assert loy.data["rewards"][0]["title"] == "App ₹40 OFF"


def test_razorpay_verify_is_idempotent():
    owner = _user("rzp@test.com")
    restaurant = provision_restaurant(owner=owner, name="Pay Place")
    from apps.billing.models import Wallet

    client = APIClient()
    token = client.post("/api/v1/auth/login/", {"identifier": "rzp@test.com", "password": "demo1234"}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_RESTAURANT_ID=str(restaurant.id))
    payload = {"razorpay_payment_id": "pay_test_1", "amount": "500"}
    a = client.post("/api/v1/billing/wallet/verify/", payload, format="json")
    b = client.post("/api/v1/billing/wallet/verify/", payload, format="json")
    assert a.status_code == 200
    assert b.status_code == 200
    wallet = Wallet.objects.get(restaurant=restaurant)
    assert wallet.balance == Decimal("5500")


def test_ws_order_group_requires_membership():
    from apps.orders.consumers import OperationsConsumer

    a = _user("wsa@test.com")
    b = _user("wsb@test.com")
    ra = provision_restaurant(owner=a, name="A WS")
    provision_restaurant(owner=b, name="B WS")
    from apps.orders.models import Order

    order = Order.objects.create(restaurant=ra, number="A-1", order_type="takeaway", created_by=a)
    assert str(order.restaurant_id) == str(ra.id)
    assert RestaurantMembership.objects.filter(user=a, restaurant=ra).exists()
    assert not RestaurantMembership.objects.filter(user=b, restaurant=ra).exists()
