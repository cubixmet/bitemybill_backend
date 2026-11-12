from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.customers.models import CustomerAddress, RestaurantCustomer
from apps.menu.models import MenuCategory, MenuItem
from apps.menu.views import MenuCategorySerializer, MenuItemSerializer
from apps.orders.models import DiningTable, Order, TableSession
from apps.orders.services import create_order
from apps.orders.views import OrderSerializer
from apps.restaurants.models import Restaurant


class StorefrontMixin:
    permission_classes = [permissions.AllowAny]

    def restaurant(self):
        return get_object_or_404(Restaurant, slug=self.kwargs["slug"], is_archived=False)


class StorefrontHomeView(StorefrontMixin, APIView):
    def get(self, request, slug):
        r = self.restaurant()
        settings_obj = getattr(r, "settings", None)
        return Response(
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "description": r.description,
                "address": r.address,
                "phone": r.phone,
                "theme": r.theme,
                "pwa_url": r.pwa_url,
                "pwa_host": r.pwa_host,
                "custom_domain": r.custom_domain,
                "settings": {
                    "tax_rate": settings_obj.tax_rate if settings_obj else 5,
                    "delivery_fee": settings_obj.delivery_fee if settings_obj else 40,
                    "packaging_fee": settings_obj.packaging_fee if settings_obj else 15,
                    "min_order": settings_obj.min_order if settings_obj else 300,
                    "opening_time": settings_obj.opening_time if settings_obj else "11:00",
                    "closing_time": settings_obj.closing_time if settings_obj else "23:00",
                },
            }
        )


class StorefrontMenuView(StorefrontMixin, APIView):
    def get(self, request, slug):
        r = self.restaurant()
        cats = MenuCategory.objects.filter(restaurant=r, deleted_at__isnull=True, is_active=True)
        items = MenuItem.objects.filter(restaurant=r, deleted_at__isnull=True).select_related("category")
        return Response(
            {
                "categories": MenuCategorySerializer(cats, many=True).data,
                "items": MenuItemSerializer(items, many=True).data,
            }
        )


class StorefrontTablesView(StorefrontMixin, APIView):
    def get(self, request, slug):
        r = self.restaurant()
        tables = DiningTable.objects.filter(restaurant=r)
        return Response([{"id": str(t.id), "code": t.code, "seats": t.seats, "status": t.status, "join_code": t.join_code} for t in tables])

    def post(self, request, slug):
        r = self.restaurant()
        code = request.data.get("join_code") or request.data.get("table_code")
        table = DiningTable.objects.filter(restaurant=r).filter(join_code=code).first() or DiningTable.objects.filter(
            restaurant=r, code=code
        ).first()
        if not table:
            return Response({"detail": "Table not found"}, status=404)
        session = TableSession.objects.filter(table=table, status=TableSession.Status.OPEN).first()
        if not session:
            session = TableSession.objects.create(restaurant=r, table=table)
            table.status = DiningTable.Status.OCCUPIED
            table.save(update_fields=["status"])
        return Response({"id": str(session.id), "table": table.code, "status": session.status})


class StorefrontCheckoutView(StorefrontMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, slug):
        r = self.restaurant()
        data = dict(request.data)
        data.setdefault("channel", Order.Channel.PWA)
        if data.get("order_type") == "dine_in":
            data["channel"] = Order.Channel.QR
        phone = request.user.phone or request.user.email
        customer, created = RestaurantCustomer.objects.get_or_create(
            restaurant=r,
            phone=phone,
            defaults={
                "name": getattr(request.user, "display_name", None)
                or getattr(request.user, "name", None)
                or phone,
                "email": request.user.email or "",
                "user": request.user,
                "source": RestaurantCustomer.Source.APP,
            },
        )
        updates = []
        if not customer.user_id:
            customer.user = request.user
            updates.append("user")
        if customer.source in (RestaurantCustomer.Source.WALK_IN, RestaurantCustomer.Source.ENQUIRY, ""):
            customer.source = RestaurantCustomer.Source.APP
            updates.append("source")
        if updates:
            customer.save(update_fields=updates + ["updated_at"])
        data["customer"] = customer.id
        data.setdefault("customer_name", customer.name)
        data.setdefault("customer_phone", customer.phone)
        from apps.orders.views import OrderWriteSerializer

        ser = OrderWriteSerializer(data=data)
        ser.is_valid(raise_exception=True)
        order = create_order(restaurant=r, user=request.user, data=ser.validated_data)
        from apps.orders.services import send_kot

        send_kot(order, request.user)
        return Response(OrderSerializer(order).data, status=201)


class StorefrontOrdersView(StorefrontMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, slug, pk=None):
        r = self.restaurant()
        qs = Order.objects.filter(restaurant=r, created_by=request.user).prefetch_related("items__modifiers")
        if pk:
            order = get_object_or_404(qs, pk=pk)
            return Response(OrderSerializer(order).data)
        return Response(OrderSerializer(qs[:50], many=True).data)


def _customer_for_user(restaurant, user):
    phone = user.phone or user.email
    customer = RestaurantCustomer.objects.filter(restaurant=restaurant, user=user).first()
    if customer:
        return customer
    if phone:
        customer = RestaurantCustomer.objects.filter(restaurant=restaurant, phone=phone).first()
        if customer:
            if not customer.user_id:
                customer.user = user
                customer.source = RestaurantCustomer.Source.APP
                customer.save(update_fields=["user", "source", "updated_at"])
            return customer
    return None


class StorefrontLoyaltyView(StorefrontMixin, APIView):
    """Public program + rewards catalog for the customer PWA (programs from shell)."""

    def get(self, request, slug):
        from apps.loyalty.models import LoyaltyEarnRule, LoyaltyProgram, Reward

        r = self.restaurant()
        program = LoyaltyProgram.objects.filter(restaurant=r, is_active=True).first()
        rewards = Reward.objects.filter(restaurant=r, is_active=True).order_by("points_cost")
        rules = []
        if program:
            rules = list(
                LoyaltyEarnRule.objects.filter(program=program, is_active=True).values(
                    "id", "kind", "label", "points", "multiplier"
                )
            )
        return Response(
            {
                "program": (
                    {
                        "id": str(program.id),
                        "name": program.name,
                        "points_per_amount": program.points_per_amount,
                        "amount_step": program.amount_step,
                    }
                    if program
                    else None
                ),
                "rules": rules,
                "rewards": [
                    {
                        "id": str(rw.id),
                        "title": rw.title,
                        "kind": rw.kind,
                        "points_cost": rw.points_cost,
                        "min_order": rw.min_order,
                        "discount_amount": rw.discount_amount,
                        "eligible_order_types": rw.eligible_order_types,
                    }
                    for rw in rewards
                ],
            }
        )


class StorefrontLoyaltyMeView(StorefrontMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, slug):
        from apps.loyalty.models import LoyaltyLedger

        r = self.restaurant()
        customer = _customer_for_user(r, request.user)
        if not customer:
            return Response(
                {"points_balance": 0, "customer": None, "ledger": [], "detail": "No customer profile yet"}
            )
        ledger = LoyaltyLedger.objects.filter(restaurant=r, customer=customer).order_by("-created_at")[:40]
        return Response(
            {
                "customer": {
                    "id": str(customer.id),
                    "name": customer.name,
                    "phone": customer.phone,
                    "source": customer.source,
                    "is_app_user": customer.is_app_user,
                },
                "points_balance": customer.points_balance,
                "ledger": [
                    {
                        "id": str(e.id),
                        "kind": e.kind,
                        "points": e.points,
                        "balance_after": e.balance_after,
                        "note": e.note,
                        "reference": e.reference,
                        "created_at": e.created_at,
                    }
                    for e in ledger
                ],
            }
        )


class StorefrontLoyaltyRedeemView(StorefrontMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, slug):
        from apps.loyalty.models import Reward
        from apps.loyalty.services import redeem_reward

        r = self.restaurant()
        customer = _customer_for_user(r, request.user)
        if not customer:
            return Response({"detail": "Customer profile not found"}, status=404)
        reward = Reward.objects.filter(restaurant=r, pk=request.data.get("reward"), is_active=True).first()
        if not reward:
            return Response({"detail": "Reward not found"}, status=404)
        try:
            redemption = redeem_reward(restaurant=r, customer=customer, reward=reward)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        customer.refresh_from_db()
        return Response(
            {
                "id": str(redemption.id),
                "points_spent": redemption.points_spent,
                "points_balance": customer.points_balance,
                "reward": reward.title,
            },
            status=201,
        )
