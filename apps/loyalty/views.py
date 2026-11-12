from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.loyalty.models import LoyaltyEarnRule, LoyaltyLedger, LoyaltyProgram, Reward, RewardRedemption
from apps.loyalty.services import redeem_reward
from apps.customers.models import RestaurantCustomer


class ProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyProgram
        fields = ("id", "name", "points_per_amount", "amount_step", "is_active")


class RuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyEarnRule
        fields = ("id", "program", "kind", "label", "points", "multiplier", "menu_item", "category", "is_active")


class RewardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reward
        fields = (
            "id",
            "title",
            "kind",
            "points_cost",
            "min_order",
            "discount_amount",
            "menu_item",
            "eligible_order_types",
            "usage_limit",
            "expires_at",
            "is_active",
        )


class LedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyLedger
        fields = ("id", "customer", "kind", "points", "balance_after", "reference", "note", "created_at")


class ProgramViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "loyalty"
    queryset = LoyaltyProgram.objects.all()
    serializer_class = ProgramSerializer


class RuleViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "loyalty"
    queryset = LoyaltyEarnRule.objects.all()
    serializer_class = RuleSerializer


class RewardViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "loyalty"
    queryset = Reward.objects.all()
    serializer_class = RewardSerializer


class LedgerViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "loyalty"
    queryset = LoyaltyLedger.objects.all()
    serializer_class = LedgerSerializer
    http_method_names = ["get", "head", "options"]


class RedeemView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "loyalty"

    def post(self, request):
        customer = RestaurantCustomer.objects.filter(
            restaurant=request.restaurant, pk=request.data.get("customer")
        ).first()
        reward = Reward.objects.filter(restaurant=request.restaurant, pk=request.data.get("reward")).first()
        if not customer or not reward:
            return Response({"detail": "Invalid customer or reward"}, status=400)
        try:
            redemption = redeem_reward(restaurant=request.restaurant, customer=customer, reward=reward)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        log_action(request, "loyalty.redeemed", redemption)
        return Response({"id": str(redemption.id), "points_spent": redemption.points_spent}, status=201)
