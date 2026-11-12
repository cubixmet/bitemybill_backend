"""Token-authenticated data API for manually designed restaurant websites."""

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.menu.models import MenuCategory, MenuItem
from apps.menu.views import MenuCategorySerializer, MenuItemSerializer
from apps.orders.models import Order
from apps.orders.services import create_order
from apps.orders.views import OrderSerializer
from apps.restaurants.partner_auth import HasStorefrontScope, IsStorefrontAPIToken, StorefrontAPITokenAuthentication


class PartnerAPIMixin:
    authentication_classes = [StorefrontAPITokenAuthentication]
    permission_classes = [IsStorefrontAPIToken, HasStorefrontScope]


class PartnerRestaurantView(PartnerAPIMixin, APIView):
    partner_scope = "restaurant"

    def get(self, request):
        r = request.restaurant
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


class PartnerMenuView(PartnerAPIMixin, APIView):
    partner_scope = "menu"

    def get(self, request):
        r = request.restaurant
        cats = MenuCategory.objects.filter(restaurant=r, deleted_at__isnull=True, is_active=True)
        items = MenuItem.objects.filter(restaurant=r, deleted_at__isnull=True).select_related("category")
        return Response(
            {
                "categories": MenuCategorySerializer(cats, many=True).data,
                "items": MenuItemSerializer(items, many=True).data,
            }
        )


class PartnerOrdersView(PartnerAPIMixin, APIView):
    partner_scope = "orders"

    def post(self, request):
        r = request.restaurant
        data = dict(request.data)
        data.setdefault("channel", Order.Channel.PWA)
        data.setdefault("order_type", Order.Type.DELIVERY)
        order = create_order(restaurant=r, user=None, data=data)
        return Response(OrderSerializer(order).data, status=201)


class PartnerLoyaltyView(PartnerAPIMixin, APIView):
    partner_scope = "loyalty"

    def get(self, request):
        from apps.loyalty.models import LoyaltyEarnRule, LoyaltyProgram, Reward

        r = request.restaurant
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
                    }
                    for rw in rewards
                ],
            }
        )
