from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.billing.models import (
    CatalogModule,
    RestaurantModule,
    UsageLine,
    UsageTransaction,
    Wallet,
    WalletTransaction,
)
from apps.restaurants.constants import (
    CATALOG_MODULES,
    DEFAULT_MODULES,
    PERMISSION_ACTIONS,
    PERMISSION_SCOPES,
    ROLE_GRANTS,
    ROLE_META,
)
from apps.restaurants.models import Permission, Restaurant, RestaurantMembership, RestaurantSettings, Role


def ensure_catalog_and_roles():
    for scope in PERMISSION_SCOPES:
        for action in PERMISSION_ACTIONS:
            Permission.objects.get_or_create(scope=scope, action=action)
    for code, (name, desc) in ROLE_META.items():
        role, _ = Role.objects.get_or_create(code=code, defaults={"name": name, "description": desc})
        grants = ROLE_GRANTS[code]
        wanted = []
        for scope, actions in grants.items():
            acts = PERMISSION_ACTIONS if actions == "*" else actions
            wanted.extend(list(Permission.objects.filter(scope=scope, action__in=acts)))
        role.permissions.set(wanted)
    aliases = {
        "orders": ["orders-pos"],
        "online": ["online-ordering", "customer-app", "customer_app"],
        "custom_storefront": ["custom-website", "white-label", "storefront-api"],
    }
    for code, name, group, price in CATALOG_MODULES:
        mod, created = CatalogModule.objects.get_or_create(
            code=code,
            defaults={"name": name, "group": group, "monthly_price": Decimal(price), "is_active": True},
        )
        if not created and (mod.name != name or mod.monthly_price != Decimal(price)):
            mod.name = name
            mod.group = group
            mod.monthly_price = Decimal(price)
            mod.save(update_fields=["name", "group", "monthly_price", "updated_at"])
        extra = aliases.get(code)
        if extra and not mod.aliases:
            mod.aliases = extra
            mod.save(update_fields=["aliases"])


def unique_slug(name: str) -> str:
    base = slugify(name)[:60] or "restaurant"
    slug = base
    i = 2
    while Restaurant.objects.filter(slug=slug).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


@transaction.atomic
def provision_restaurant(*, owner, name, restaurant_type="", address="", owner_name="", email="", phone="", modules=None):
    ensure_catalog_and_roles()
    initials = "".join([p[0] for p in name.split()[:2]]).upper() or "R"
    restaurant = Restaurant.objects.create(
        name=name,
        slug=unique_slug(name),
        owner_name=owner_name or owner.name,
        email=email or owner.email,
        phone=phone or owner.phone,
        restaurant_type=restaurant_type,
        address=address,
        initials=initials,
        order_prefix=initials[:3] or "BB",
    )
    RestaurantSettings.objects.create(restaurant=restaurant)
    Wallet.objects.create(restaurant=restaurant, balance=Decimal("5000.00"))
    owner_role = Role.objects.get(code="owner")
    RestaurantMembership.objects.create(user=owner, restaurant=restaurant, role=owner_role)
    codes = list(modules or DEFAULT_MODULES)
    # Free todos module is always on — never billed
    if "todos" not in codes:
        codes.append("todos")
    for code in codes:
        mod = CatalogModule.objects.filter(code=code).first()
        if not mod:
            continue
        RestaurantModule.objects.get_or_create(restaurant=restaurant, module=mod, defaults={"enabled": True})
    if "expenses" in codes:
        from apps.expenses.defaults import ensure_default_expense_categories

        ensure_default_expense_categories(restaurant)
    if "accounting" in codes:
        from apps.accounting.defaults import ensure_chart_of_accounts

        ensure_chart_of_accounts(restaurant)
    return restaurant


def enabled_module_codes(restaurant):
    return list(
        RestaurantModule.objects.filter(restaurant=restaurant, enabled=True).values_list("module__code", flat=True)
    )


def monthly_usage_total(restaurant):
    total = Decimal("0")
    lines = []
    qs = RestaurantModule.objects.filter(restaurant=restaurant, enabled=True).select_related("module")
    for link in qs:
        amt = link.module.monthly_price
        total += amt
        lines.append((link.module, link.module.name, amt))
    return total, lines


@transaction.atomic
def apply_top_up(restaurant, amount: Decimal, *, method="Razorpay", payment_id=None, description=None):
    wallet, _ = Wallet.objects.get_or_create(restaurant=restaurant)
    amount = Decimal(amount)
    wallet.balance += amount
    txs = []
    txs.append(
        WalletTransaction.objects.create(
            wallet=wallet,
            restaurant=restaurant,
            entry_type=WalletTransaction.Type.TOP_UP,
            amount=amount,
            balance_after=wallet.balance,
            description=description or f"Wallet top-up via {method}",
            method=method,
            razorpay_payment_id=payment_id or None,
        )
    )
    outstanding = restaurant.outstanding_amount
    if outstanding > 0:
        settled = min(wallet.balance, outstanding)
        wallet.balance -= settled
        restaurant.outstanding_amount = outstanding - settled
        WalletTransaction.objects.create(
            wallet=wallet,
            restaurant=restaurant,
            entry_type=WalletTransaction.Type.DUES_RECOVERY,
            amount=-settled,
            balance_after=wallet.balance,
            status=WalletTransaction.Status.SUCCESS if restaurant.outstanding_amount == 0 else WalletTransaction.Status.PARTIAL,
            description="Outstanding usage dues recovery",
        )
        if restaurant.outstanding_amount == 0:
            restaurant.account_status = Restaurant.AccountStatus.ACTIVE
            restaurant.grace_months_used = 0
        restaurant.save(update_fields=["outstanding_amount", "account_status", "grace_months_used", "updated_at"])
    wallet.save(update_fields=["balance", "updated_at"])
    return wallet


@transaction.atomic
def apply_monthly_debit(restaurant):
    total, lines = monthly_usage_total(restaurant)
    wallet, _ = Wallet.objects.get_or_create(restaurant=restaurant)
    debited = min(wallet.balance, total)
    shortfall = total - debited
    wallet.balance -= debited
    restaurant.outstanding_amount += shortfall
    if shortfall > 0:
        restaurant.grace_months_used += 1
        if restaurant.grace_months_used >= settings.GRACE_MONTHS:
            restaurant.account_status = Restaurant.AccountStatus.SCHEDULED_FOR_DELETION
        else:
            restaurant.account_status = Restaurant.AccountStatus.PAYMENT_DUE
    else:
        restaurant.grace_months_used = 0
        restaurant.account_status = Restaurant.AccountStatus.ACTIVE
    period = timezone.now().strftime("%B %Y")
    wtx = WalletTransaction.objects.create(
        wallet=wallet,
        restaurant=restaurant,
        entry_type=WalletTransaction.Type.USAGE_DEBIT,
        amount=-debited,
        balance_after=wallet.balance,
        status=WalletTransaction.Status.SUCCESS if shortfall == 0 else (WalletTransaction.Status.PARTIAL if debited else WalletTransaction.Status.FAILED),
        description=f"{period} module usage",
    )
    usage = UsageTransaction.objects.create(
        restaurant=restaurant,
        period=period,
        total=total,
        debited=debited,
        outstanding=shortfall,
        balance_after=wallet.balance,
        status=UsageTransaction.Status.PAID if shortfall == 0 else (UsageTransaction.Status.PARTIALLY_PAID if debited else UsageTransaction.Status.UNPAID),
        wallet_transaction=wtx,
    )
    for module, label, amount in lines:
        UsageLine.objects.create(usage=usage, module=module, label=label, amount=amount)
    wallet.save(update_fields=["balance", "updated_at"])
    restaurant.save(update_fields=["outstanding_amount", "grace_months_used", "account_status", "updated_at"])
    return usage
