from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.billing.models import RestaurantModule, Wallet
from apps.billing.services import ensure_catalog_and_roles, provision_restaurant
from apps.customers.models import RestaurantCustomer
from apps.inventory.models import (
    InventoryItem,
    InventoryLedger,
    PurchaseItem,
    PurchaseOrder,
    Supplier,
    Unit,
)
from apps.inventory.services import receive_purchase, settle_purchase
from apps.loyalty.models import LoyaltyProgram, Reward
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import DiningTable
from apps.production.models import Recipe, RecipeIngredient
from apps.restaurants.constants import DEFAULT_MODULES
from apps.restaurants.models import RestaurantMembership, Role
from apps.staff.models import StaffProfile

User = get_user_model()


class Command(BaseCommand):
    help = "Seed demo catalog, users, and restaurants"

    def handle(self, *args, **opts):
        ensure_catalog_and_roles()
        units = {
            "kg": Unit.objects.get_or_create(code="kg", defaults={"name": "Kilogram", "kind": "mass", "to_base": Decimal("1")})[0],
            "g": Unit.objects.get_or_create(code="g", defaults={"name": "Gram", "kind": "mass", "to_base": Decimal("0.001")})[0],
            "l": Unit.objects.get_or_create(code="l", defaults={"name": "Litre", "kind": "volume", "to_base": Decimal("1")})[0],
            "ml": Unit.objects.get_or_create(code="ml", defaults={"name": "Millilitre", "kind": "volume", "to_base": Decimal("0.001")})[0],
            "pcs": Unit.objects.get_or_create(code="pcs", defaults={"name": "Pieces", "kind": "count", "to_base": Decimal("1")})[0],
        }

        admin, _ = User.objects.get_or_create(
            email="admin@bitemybills.tech",
            defaults={"user_kind": "platform", "display_name": "Platform Admin", "is_staff": True, "is_superuser": True},
        )
        admin.set_password("Admin@123")
        admin.user_kind = "platform"
        admin.is_staff = True
        admin.save()

        ahmed, _ = User.objects.get_or_create(
            email="owner@malabarkitchen.com",
            defaults={"display_name": "Ahmed", "phone": "+919847011223", "user_kind": "staff"},
        )
        ahmed.set_password("demo1234")
        ahmed.display_name = "Ahmed"
        ahmed.save()

        customer, _ = User.objects.get_or_create(
            email="afsal@example.com",
            defaults={"display_name": "Afsal Rahman", "phone": "+919846033211", "user_kind": "customer"},
        )
        customer.set_password("demo1234")
        customer.user_kind = "customer"
        customer.save()

        specs = [
            {
                "name": "Malabar Kitchen",
                "slug": "malabar-kitchen",
                "role": "owner",
                "theme": "fresh-green",
                "city": "Kochi",
                "prefix": "MK",
                "modules": DEFAULT_MODULES
                + [
                    "customers",
                    "loyalty",
                    "online",
                    "custom_storefront",
                    "delivery",
                    "inventory",
                    "procurement",
                    "production",
                    "staff",
                    "expenses",
                    "accounting",
                ],
            },
            {
                "name": "Urban Bites",
                "slug": "urban-bites",
                "role": "manager",
                "theme": "classic-blue",
                "city": "Bengaluru",
                "prefix": "UB",
                "modules": [
                    "orders",
                    "tables",
                    "kitchen",
                    "billing",
                    "menu",
                    "inventory",
                    "customers",
                    "loyalty",
                    "online",
                    "custom_storefront",
                    "delivery",
                    "staff",
                    "expenses",
                    "accounting",
                ],
            },
            {
                "name": "Royal Table",
                "slug": "royal-table",
                "role": "accountant",
                "theme": "premium-dark",
                "city": "Chennai",
                "prefix": "RT",
                "modules": DEFAULT_MODULES + ["expenses", "accounting", "payroll", "staff"],
            },
        ]
        from apps.restaurants.models import Restaurant
        from apps.billing.models import CatalogModule

        for spec in specs:
            restaurant = Restaurant.objects.filter(slug=spec["slug"]).first()
            if not restaurant:
                restaurant = provision_restaurant(
                    owner=ahmed,
                    name=spec["name"],
                    restaurant_type="Casual Dining",
                    address=f"{spec['city']}, India",
                    owner_name="Ahmed",
                    email=ahmed.email,
                    phone=ahmed.phone,
                    modules=spec["modules"],
                )
                restaurant.slug = spec["slug"]
                restaurant.theme = spec["theme"]
                restaurant.city = spec["city"]
                restaurant.order_prefix = spec["prefix"]
                restaurant.initials = spec["prefix"][:2]
                restaurant.save()
            for code in spec["modules"]:
                mod = CatalogModule.objects.filter(code=code).first()
                if mod:
                    link, _ = RestaurantModule.objects.get_or_create(
                        restaurant=restaurant, module=mod, defaults={"enabled": True}
                    )
                    if not link.enabled:
                        link.enabled = True
                        link.save(update_fields=["enabled", "updated_at"])
            if "accounting" in spec["modules"]:
                from apps.accounting.defaults import ensure_chart_of_accounts

                ensure_chart_of_accounts(restaurant)
            if "expenses" in spec["modules"]:
                from apps.expenses.defaults import ensure_default_expense_categories

                ensure_default_expense_categories(restaurant)
            role = Role.objects.get(code=spec["role"])
            mem, _ = RestaurantMembership.objects.get_or_create(
                user=ahmed, restaurant=restaurant, defaults={"role": role}
            )
            mem.role = role
            mem.save()
            wallet, _ = Wallet.objects.get_or_create(restaurant=restaurant)
            if wallet.balance == 0:
                wallet.balance = Decimal("12500")
                wallet.save()
            self._ops(restaurant, units, customer)
        self.stdout.write(self.style.SUCCESS("Demo seed complete. Login owner@malabarkitchen.com / demo1234"))

    def _ops(self, restaurant, units, customer_user):
        from apps.restaurants.models import RestaurantSettings

        # Pandikkad / Kerala demo coordinates — enable location login for staff geofencing demos
        settings_obj, _ = RestaurantSettings.objects.get_or_create(restaurant=restaurant)
        if settings_obj.latitude is None:
            settings_obj.latitude = Decimal("11.012800")
            settings_obj.longitude = Decimal("76.218400")
            settings_obj.login_radius_m = 250
            # Off by default so owners can sign in remotely; flip on in Settings for staff lock
            settings_obj.require_location_login = False
            settings_obj.save()

        cat, _ = MenuCategory.objects.get_or_create(
            restaurant=restaurant, slug="mains", defaults={"name": "Mains", "sort_order": 1}
        )
        biryani, _ = MenuItem.objects.get_or_create(
            restaurant=restaurant,
            name="Chicken Biriyani",
            defaults={"category": cat, "price": Decimal("280"), "cost": Decimal("140"), "station": "Biriyani", "is_veg": False},
        )
        porotta, _ = MenuItem.objects.get_or_create(
            restaurant=restaurant,
            name="Porotta",
            defaults={"category": cat, "price": Decimal("20"), "cost": Decimal("8"), "station": "Porotta", "is_veg": True},
        )
        for i, seats in enumerate([2, 4, 4, 6, 2, 8], start=1):
            DiningTable.objects.get_or_create(
                restaurant=restaurant,
                code=f"T-{i:02d}",
                defaults={"zone": "Main", "seats": seats, "join_code": f"{restaurant.order_prefix}-{1000 + i}"},
            )
        maida, _ = InventoryItem.objects.get_or_create(
            restaurant=restaurant,
            name="Maida",
            defaults={"category": "Dry Goods", "unit": units["kg"], "quantity": Decimal("48"), "minimum": Decimal("40"), "cost": Decimal("42")},
        )
        sugar, _ = InventoryItem.objects.get_or_create(
            restaurant=restaurant,
            name="Sugar",
            defaults={"category": "Dry Goods", "unit": units["kg"], "quantity": Decimal("26"), "minimum": Decimal("25"), "cost": Decimal("46")},
        )
        salt, _ = InventoryItem.objects.get_or_create(
            restaurant=restaurant,
            name="Salt",
            defaults={"category": "Dry Goods", "unit": units["kg"], "quantity": Decimal("34"), "minimum": Decimal("15"), "cost": Decimal("18")},
        )
        oil, _ = InventoryItem.objects.get_or_create(
            restaurant=restaurant,
            name="Cooking Oil",
            defaults={"category": "Oils", "unit": units["l"], "quantity": Decimal("62"), "minimum": Decimal("50"), "cost": Decimal("138")},
        )
        if not InventoryLedger.objects.filter(item=maida).exists():
            InventoryLedger.objects.create(
                restaurant=restaurant,
                item=maida,
                entry_type=InventoryLedger.Type.OPENING,
                quantity=maida.quantity,
                balance_after=maida.quantity,
                note="Opening stock",
            )

        nagarjuna, _ = Supplier.objects.get_or_create(
            restaurant=restaurant,
            name="Nagarjuna Traders",
            defaults={
                "contact": "Ravi Nair",
                "phone": "+91 98470 11223",
                "email": "sales@nagarjuna.example",
                "payment_terms": "Net 15",
            },
        )
        kochi_flour, _ = Supplier.objects.get_or_create(
            restaurant=restaurant,
            name="Kochi Flour Mills",
            defaults={
                "contact": "Rajesh Pillai",
                "phone": "+91 98460 22114",
                "email": "sales@kochiflour.example",
                "payment_terms": "Net 15",
            },
        )
        # Credit purchase: 5 kg sugar @ ₹100 = ₹500 (unit cost history + pending payable)
        if not PurchaseOrder.objects.filter(restaurant=restaurant, number="PO-SUGAR-CREDIT").exists():
            sugar_po = PurchaseOrder.objects.create(
                restaurant=restaurant,
                supplier=nagarjuna,
                number="PO-SUGAR-CREDIT",
                status=PurchaseOrder.Status.ORDERED,
                is_credit=True,
                tax_rate=Decimal("0"),
                notes="Demo credit purchase — settle partially or in full",
            )
            PurchaseItem.objects.create(
                purchase=sugar_po,
                item=sugar,
                quantity=Decimal("5"),
                unit_cost=Decimal("100"),
            )
            sugar_po.recompute_total()
            receive_purchase(sugar_po)
            settle_purchase(sugar_po, amount=Decimal("200"), method="upi", note="Partial settlement demo")

        if not PurchaseOrder.objects.filter(restaurant=restaurant, number="PO-MAIDA-CASH").exists():
            maida_po = PurchaseOrder.objects.create(
                restaurant=restaurant,
                supplier=kochi_flour,
                number="PO-MAIDA-CASH",
                status=PurchaseOrder.Status.ORDERED,
                is_credit=False,
                tax_rate=Decimal("5"),
                notes="Paid on delivery",
            )
            PurchaseItem.objects.create(
                purchase=maida_po,
                item=maida,
                quantity=Decimal("25"),
                unit_cost=Decimal("42"),
            )
            maida_po.recompute_total()
            receive_purchase(maida_po)

        recipe, created = Recipe.objects.get_or_create(
            restaurant=restaurant,
            name="Porotta - 1 KG",
            defaults={
                "category": "Porotta",
                "yield_qty": Decimal("40"),
                "yield_unit": "Porottas",
                "batch_time_mins": 90,
                "station": "Porotta",
                "steps": ["Mix", "Rest", "Roast"],
            },
        )
        if created:
            RecipeIngredient.objects.create(recipe=recipe, inventory_item=maida, quantity=Decimal("1"), unit_code="kg")
            RecipeIngredient.objects.create(recipe=recipe, inventory_item=sugar, quantity=Decimal("200"), unit_code="g")
            RecipeIngredient.objects.create(recipe=recipe, inventory_item=salt, quantity=Decimal("100"), unit_code="g")
            RecipeIngredient.objects.create(recipe=recipe, inventory_item=oil, quantity=Decimal("50"), unit_code="ml")

        # Watermelon juice — 5 kg fruit with 500 g shell wastage tracked in inventory
        watermelon, _ = InventoryItem.objects.get_or_create(
            restaurant=restaurant,
            name="Watermelon",
            defaults={
                "category": "Produce",
                "unit": units["kg"],
                "quantity": Decimal("25"),
                "minimum": Decimal("5"),
                "cost": Decimal("30"),
            },
        )
        juice_output, _ = InventoryItem.objects.get_or_create(
            restaurant=restaurant,
            name="Watermelon Juice",
            defaults={
                "category": "Prepared",
                "unit": units["l"],
                "quantity": Decimal("0"),
                "minimum": Decimal("2"),
                "cost": Decimal("80"),
            },
        )
        juice_recipe, juice_created = Recipe.objects.get_or_create(
            restaurant=restaurant,
            name="Watermelon Juice - 5 KG",
            defaults={
                "category": "Beverages",
                "yield_qty": Decimal("4"),
                "yield_unit": "l",
                "batch_time_mins": 20,
                "station": "Beverages",
                "prep_type": "Made to Order",
                "output_item": juice_output,
                "steps": ["Wash", "Cut and discard shell", "Blend flesh", "Strain and chill"],
            },
        )
        if not juice_recipe.output_item_id:
            juice_recipe.output_item = juice_output
            juice_recipe.save(update_fields=["output_item", "updated_at"])
        if juice_created:
            RecipeIngredient.objects.create(
                recipe=juice_recipe,
                inventory_item=watermelon,
                quantity=Decimal("5"),
                unit_code="kg",
                wastage_qty=Decimal("0.5"),
                wastage_pct=Decimal("0"),
                wastage_note="shell",
                note="Whole fruit; 500g shell discarded",
            )
        juice_recipe.electricity_cost = Decimal("12")
        juice_recipe.gas_fuel_cost = Decimal("0")
        juice_recipe.labor_cost = Decimal("40")
        juice_recipe.selling_price = Decimal("480")
        juice_recipe.save(
            update_fields=["electricity_cost", "gas_fuel_cost", "labor_cost", "selling_price", "updated_at"]
        )

        LoyaltyProgram.objects.get_or_create(
            restaurant=restaurant, defaults={"name": "Guest Rewards", "points_per_amount": Decimal("1"), "amount_step": Decimal("100")}
        )
        Reward.objects.get_or_create(
            restaurant=restaurant,
            title="₹50 OFF",
            defaults={"kind": Reward.Kind.DISCOUNT, "points_cost": Decimal("100"), "min_order": Decimal("300"), "discount_amount": Decimal("50")},
        )
        Reward.objects.get_or_create(
            restaurant=restaurant,
            title="FREE POROTTA",
            defaults={"kind": Reward.Kind.FREE_ITEM, "points_cost": Decimal("150"), "menu_item": porotta},
        )
        from django.utils import timezone

        RestaurantCustomer.objects.get_or_create(
            restaurant=restaurant,
            phone=customer_user.phone,
            defaults={
                "user": customer_user,
                "name": customer_user.display_name,
                "email": customer_user.email,
                "status": "vip",
                "source": RestaurantCustomer.Source.APP,
                "points_balance": Decimal("750"),
            },
        )
        RestaurantCustomer.objects.get_or_create(
            restaurant=restaurant,
            phone="+919847011299",
            defaults={
                "name": "Priya Enquiry",
                "email": "priya.enq@example.com",
                "status": "new",
                "source": RestaurantCustomer.Source.ENQUIRY,
                "enquiry_note": "Asked about weekend banquet catering for 40 guests",
                "enquiry_at": timezone.now(),
            },
        )
        RestaurantCustomer.objects.get_or_create(
            restaurant=restaurant,
            phone="+919846022188",
            defaults={
                "name": "Arun Delivery",
                "status": "regular",
                "source": RestaurantCustomer.Source.DELIVERY,
                "points_balance": Decimal("120"),
            },
        )

        from apps.delivery.models import DeliveryAgent
        from apps.expenses.models import Expense, ExpenseCategory
        from apps.notifications.models import Notification

        StaffProfile.objects.get_or_create(
            restaurant=restaurant,
            name="Bibin Joy",
            defaults={"role_title": "Kitchen Staff", "wage_type": "daily", "wage_amount": Decimal("750"), "phone": "+91 98950 77004"},
        )
        StaffProfile.objects.get_or_create(
            restaurant=restaurant,
            name="Deepa Krishnan",
            defaults={"role_title": "Manager", "wage_type": "monthly", "wage_amount": Decimal("42000"), "phone": "+91 98470 22110"},
        )
        from apps.expenses.defaults import ensure_default_expense_categories

        ensure_default_expense_categories(restaurant)
        utilities = ExpenseCategory.objects.filter(restaurant=restaurant, parent=None, name="Utilities").first()
        electricity = (
            ExpenseCategory.objects.filter(restaurant=restaurant, parent=utilities, name="Electricity").first()
            if utilities
            else None
        )
        if electricity:
            Expense.objects.get_or_create(
                restaurant=restaurant,
                payee="KSEB",
                defaults={
                    "category": electricity,
                    "amount": Decimal("18640"),
                    "date": timezone.localdate(),
                    "method": "UPI",
                    "note": "Electricity",
                },
            )
        rent_child = ExpenseCategory.objects.filter(
            restaurant=restaurant, parent__name="Rent", name="Shop / premises rent"
        ).first()
        if rent_child:
            Expense.objects.get_or_create(
                restaurant=restaurant,
                payee="Marine Drive Properties",
                defaults={
                    "category": rent_child,
                    "amount": Decimal("85000"),
                    "date": timezone.localdate(),
                    "method": "Bank Transfer",
                    "note": "Monthly shop rent",
                    "is_recurring": True,
                    "approved": True,
                },
            )
        Notification.objects.get_or_create(
            restaurant=restaurant,
            title="Welcome to BiteMyBills",
            defaults={"body": "Live operations are connected to the API.", "kind": "info"},
        )
        DeliveryAgent.objects.get_or_create(
            restaurant=restaurant,
            name="Vinod Kumar",
            defaults={"phone": "+91 90880 55190", "vehicle": "Honda Activa", "is_available": True},
        )
