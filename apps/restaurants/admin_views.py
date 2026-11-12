from django.contrib.auth import get_user_model
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import CatalogModule, SetupRequest, UsageTransaction, Wallet, WalletTransaction
from apps.billing.services import apply_monthly_debit
from apps.common.permissions import IsPlatformAdmin
from apps.restaurants.models import Restaurant, RestaurantMembership
from apps.restaurants.serializers import CatalogModuleSerializer, RestaurantSerializer

User = get_user_model()


class AdminRestaurantList(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        qs = Restaurant.objects.all()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(account_status=status_filter)
        return Response(RestaurantSerializer(qs[:200], many=True, context={"request": request}).data)


class AdminWalletList(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        data = []
        for w in Wallet.objects.select_related("restaurant"):
            data.append(
                {
                    "restaurant_id": str(w.restaurant_id),
                    "restaurant": w.restaurant.name,
                    "balance": w.balance,
                    "status": w.restaurant.account_status,
                    "outstanding": w.restaurant.outstanding_amount,
                    "grace_months_used": w.restaurant.grace_months_used,
                }
            )
        return Response(data)


class AdminUsageList(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        qs = UsageTransaction.objects.select_related("restaurant").prefetch_related("lines")[:200]
        return Response(
            [
                {
                    "id": str(u.id),
                    "restaurant": u.restaurant.name,
                    "period": u.period,
                    "total": u.total,
                    "debited": u.debited,
                    "outstanding": u.outstanding,
                    "status": u.status,
                }
                for u in qs
            ]
        )


class AdminUsers(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        users = User.objects.all()[:200]
        return Response(
            [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "name": u.name,
                    "kind": u.user_kind,
                    "is_active": u.is_active,
                }
                for u in users
            ]
        )


class AdminLeads(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response(
            [
                {
                    "id": str(l.id),
                    "restaurant_name": l.restaurant_name,
                    "owner_name": l.owner_name,
                    "email": l.email,
                    "phone": l.phone,
                    "monthly_total": l.monthly_total,
                    "modules": l.module_codes,
                    "kind": l.kind,
                    "created_at": l.created_at,
                }
                for l in SetupRequest.objects.all()[:200]
            ]
        )


class AdminCatalog(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response(CatalogModuleSerializer(CatalogModule.objects.all(), many=True).data)

    def patch(self, request):
        code = request.data.get("code")
        mod = CatalogModule.objects.filter(code=code).first()
        if not mod:
            return Response({"detail": "Unknown module"}, status=404)
        if "monthly_price" in request.data:
            mod.monthly_price = request.data["monthly_price"]
        if "is_active" in request.data:
            mod.is_active = bool(request.data["is_active"])
        if "name" in request.data:
            mod.name = request.data["name"]
        mod.save()
        return Response(CatalogModuleSerializer(mod).data)


class AdminRunDebits(APIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request):
        count = 0
        for restaurant in Restaurant.objects.filter(is_archived=False):
            apply_monthly_debit(restaurant)
            count += 1
        return Response({"ran": count})
