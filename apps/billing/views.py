import hmac
import hashlib
import json
import uuid
from decimal import Decimal

from django.conf import settings
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import CatalogModule, RazorpayOrder, SetupRequest, UsageTransaction, Wallet, WalletTransaction
from apps.billing.services import apply_monthly_debit, apply_top_up, monthly_usage_total
from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission, IsPlatformAdmin
from apps.restaurants.serializers import CatalogModuleSerializer


class CatalogView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        qs = CatalogModule.objects.filter(is_active=True)
        return Response(CatalogModuleSerializer(qs, many=True).data)


class LeadView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        raw = request.data.get("module_codes") or request.data.get("modules") or []
        alias_map = {}
        for m in CatalogModule.objects.filter(is_active=True):
            alias_map[m.code] = m.code
            for alias in m.aliases or []:
                alias_map[alias] = m.code
        codes = [alias_map.get(c, c) for c in raw]
        mods = CatalogModule.objects.filter(code__in=codes, is_active=True)
        total = sum((m.monthly_price for m in mods), Decimal("0"))
        lead = SetupRequest.objects.create(
            restaurant_name=request.data.get("restaurant_name") or request.data.get("restaurant") or "",
            owner_name=request.data.get("owner_name") or request.data.get("name") or "",
            phone=request.data.get("phone") or "",
            email=request.data.get("email") or "unknown@example.com",
            city=request.data.get("city") or "",
            message=request.data.get("message") or "",
            module_codes=list(mods.values_list("code", flat=True)),
            monthly_total=total,
            restaurant_type=request.data.get("restaurant_type") or "",
            kind=request.data.get("kind") or "setup",
        )
        return Response(
            {"id": str(lead.id), "monthly_total": lead.monthly_total, "modules": lead.module_codes},
            status=201,
        )


class WalletView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "subscription"

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(restaurant=request.restaurant)
        total, lines = monthly_usage_total(request.restaurant)
        txs = WalletTransaction.objects.filter(restaurant=request.restaurant)[:50]
        usage = UsageTransaction.objects.filter(restaurant=request.restaurant).prefetch_related("lines")[:24]
        return Response(
            {
                "balance": wallet.balance,
                "status": request.restaurant.account_status,
                "outstanding": request.restaurant.outstanding_amount,
                "grace_months_used": request.restaurant.grace_months_used,
                "grace_months_left": max(0, settings.GRACE_MONTHS - request.restaurant.grace_months_used),
                "monthly_usage": total,
                "usage_lines": [{"module": m.code, "label": label, "amount": amt} for m, label, amt in lines],
                "transactions": [
                    {
                        "id": str(t.id),
                        "type": t.entry_type,
                        "amount": t.amount,
                        "balance_after": t.balance_after,
                        "status": t.status,
                        "description": t.description,
                        "date": t.created_at,
                    }
                    for t in txs
                ],
                "usage": [
                    {
                        "id": str(u.id),
                        "period": u.period,
                        "total": u.total,
                        "debited": u.debited,
                        "outstanding": u.outstanding,
                        "status": u.status,
                        "lines": [{"label": l.label, "amount": l.amount} for l in u.lines.all()],
                    }
                    for u in usage
                ],
            }
        )


class WalletTopUpView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "subscription"
    permission_action = "Edit"

    def post(self, request):
        amount = Decimal(str(request.data.get("amount") or 0))
        if amount <= 0:
            return Response({"detail": "Invalid amount"}, status=400)
        if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
            import razorpay

            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            order = client.order.create(
                {
                    "amount": int(amount * 100),
                    "currency": "INR",
                    "notes": {"restaurant_id": str(request.restaurant.id), "user_id": str(request.user.id)},
                }
            )
            RazorpayOrder.objects.create(
                restaurant=request.restaurant,
                user=request.user,
                amount=amount,
                razorpay_order_id=order["id"],
            )
            return Response({"order_id": order["id"], "amount": amount, "key_id": settings.RAZORPAY_KEY_ID})
        wallet = apply_top_up(request.restaurant, amount, method=request.data.get("method") or "Demo")
        log_action(request, "wallet.credited", wallet, {"amount": str(amount)})
        return Response({"balance": wallet.balance, "demo": True})


class WalletVerifyView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "subscription"

    def post(self, request):
        payment_id = request.data.get("razorpay_payment_id")
        amount = Decimal(str(request.data.get("amount") or 0))
        if WalletTransaction.objects.filter(razorpay_payment_id=payment_id).exists():
            return Response({"detail": "already credited"})
        wallet = apply_top_up(
            request.restaurant, amount, method="Razorpay", payment_id=payment_id
        )
        log_action(request, "wallet.credited", wallet, {"payment_id": payment_id})
        return Response({"balance": wallet.balance})


class RunUsageView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "subscription"
    permission_action = "Edit"

    def post(self, request):
        usage = apply_monthly_debit(request.restaurant)
        log_action(request, "wallet.debited", usage, {"total": str(usage.total)})
        return Response({"id": str(usage.id), "status": usage.status, "outstanding": usage.outstanding})


class RazorpayWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        payload = request.body
        sig = request.headers.get("X-Razorpay-Signature", "")
        if secret:
            expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, sig):
                return Response(status=400)
        data = json.loads(payload.decode() or "{}")
        entity = (data.get("payload") or {}).get("payment", {}).get("entity") or {}
        payment_id = entity.get("id")
        if not payment_id:
            return Response({"ok": True})
        if WalletTransaction.objects.filter(razorpay_payment_id=payment_id).exists():
            return Response({"ok": True})
        notes = entity.get("notes") or {}
        restaurant_id = notes.get("restaurant_id")
        if not restaurant_id:
            return Response({"ok": True})
        from apps.restaurants.models import Restaurant

        restaurant = Restaurant.objects.filter(pk=restaurant_id).first()
        if not restaurant:
            return Response({"ok": True})
        amount = Decimal(entity.get("amount", 0)) / Decimal("100")
        apply_top_up(restaurant, amount, method="Razorpay", payment_id=payment_id)
        return Response({"ok": True})
