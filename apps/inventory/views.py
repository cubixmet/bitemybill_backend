from decimal import Decimal, InvalidOperation

from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.inventory.models import (
    InventoryItem,
    InventoryLedger,
    PurchaseItem,
    PurchaseOrder,
    PurchasePayment,
    StockReturn,
    Supplier,
    Unit,
)
from apps.inventory.services import post_ledger, receive_purchase, settle_purchase


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ("id", "code", "name", "kind", "to_base")


class InventoryItemSerializer(serializers.ModelSerializer):
    unit_code = serializers.CharField(source="unit.code", read_only=True)

    class Meta:
        model = InventoryItem
        fields = (
            "id",
            "name",
            "sku",
            "category",
            "unit",
            "unit_code",
            "quantity",
            "minimum",
            "cost",
            "supplier_name",
            "expiry",
        )
        read_only_fields = ("quantity",)


class LedgerSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = InventoryLedger
        fields = (
            "id",
            "item",
            "item_name",
            "entry_type",
            "quantity",
            "balance_after",
            "unit_cost",
            "amount",
            "reference",
            "note",
            "created_at",
        )


class SupplierSerializer(serializers.ModelSerializer):
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = Supplier
        fields = ("id", "name", "contact", "phone", "email", "address", "payment_terms", "outstanding")

    def get_outstanding(self, obj):
        return obj.outstanding_balance()


class PurchaseLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    unit_code = serializers.CharField(source="item.unit.code", read_only=True)
    line_amount = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseItem
        fields = (
            "id",
            "item",
            "item_name",
            "unit_code",
            "quantity",
            "unit_cost",
            "line_amount",
            "received_qty",
        )
        read_only_fields = ("id", "item_name", "unit_code", "line_amount", "received_qty")

    def get_line_amount(self, obj):
        return obj.line_amount


class PurchasePaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchasePayment
        fields = ("id", "amount", "method", "paid_at", "note", "reference", "created_at")
        read_only_fields = ("id", "created_at")


class PurchaseSerializer(serializers.ModelSerializer):
    lines = PurchaseLineSerializer(many=True, required=False)
    payments = PurchasePaymentSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    pending_amount = serializers.SerializerMethodField()
    raised_by = serializers.SerializerMethodField()
    number = serializers.CharField(required=False, allow_blank=True, max_length=32)

    class Meta:
        model = PurchaseOrder
        fields = (
            "id",
            "supplier",
            "supplier_name",
            "number",
            "status",
            "is_credit",
            "payment_status",
            "amount_paid",
            "pending_amount",
            "tax_rate",
            "total",
            "due_date",
            "notes",
            "lines",
            "payments",
            "raised_by",
            "created_at",
        )
        read_only_fields = ("total", "amount_paid", "payment_status")

    def get_pending_amount(self, obj):
        return obj.pending_amount

    def get_raised_by(self, obj):
        if obj.created_by_id:
            return getattr(obj.created_by, "display_name", None) or getattr(obj.created_by, "email", "")
        return ""

    def _sync_lines(self, po, lines):
        po.lines.all().delete()
        for row in lines:
            PurchaseItem.objects.create(
                purchase=po,
                item=row["item"],
                quantity=row["quantity"],
                unit_cost=row["unit_cost"],
                received_qty=row.get("received_qty") or Decimal("0"),
            )
        po.recompute_total(save=True)

    def create(self, validated):
        lines = validated.pop("lines", [])
        # Default credit purchase unless explicitly cash
        if "is_credit" not in validated:
            validated["is_credit"] = True
        if not validated.get("number"):
            validated["number"] = f"PO-{PurchaseOrder.objects.filter(restaurant=validated['restaurant']).count() + 1:04d}"
        po = PurchaseOrder.objects.create(**validated)
        if lines:
            self._sync_lines(po, lines)
        else:
            po.recompute_total(save=True)
        return po

    def update(self, instance, validated):
        lines = validated.pop("lines", None)
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()
        if lines is not None:
            if instance.status == PurchaseOrder.Status.RECEIVED:
                raise serializers.ValidationError("Cannot change lines after goods are received")
            self._sync_lines(instance, lines)
        else:
            instance.recompute_total(save=True)
        return instance


class ReturnSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockReturn
        fields = ("id", "supplier", "item", "quantity", "reason", "status", "created_at")


class UnitViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "inventory"
    queryset = Unit.objects.all()
    serializer_class = UnitSerializer

    def get_queryset(self):
        return Unit.objects.all()


class ItemViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "inventory"
    queryset = InventoryItem.objects.select_related("unit")
    serializer_class = InventoryItemSerializer
    search_fields = ["name", "sku"]

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        item = self.get_object()
        qty = request.data.get("quantity")
        note = request.data.get("note") or "Adjustment"
        entry = post_ledger(
            restaurant=request.restaurant,
            item=item,
            entry_type=InventoryLedger.Type.ADJUSTMENT,
            quantity=qty,
            user=request.user,
            note=note,
        )
        log_action(request, "inventory.adjusted", item, {"quantity": str(qty)})
        return Response(LedgerSerializer(entry).data)

    @action(detail=True, methods=["get"], url_path="cost-history")
    def cost_history(self, request, pk=None):
        """Per-purchase unit cost history so price differences over time are visible."""
        item = self.get_object()
        rows = (
            PurchaseItem.objects.filter(item=item, purchase__restaurant=request.restaurant)
            .exclude(purchase__status=PurchaseOrder.Status.CANCELLED)
            .select_related("purchase", "purchase__supplier")
            .order_by("-purchase__created_at", "-created_at")[:50]
        )
        data = [
            {
                "purchase_id": str(r.purchase_id),
                "number": r.purchase.number,
                "supplier": r.purchase.supplier.name if r.purchase.supplier_id else "",
                "date": r.purchase.created_at.date().isoformat(),
                "quantity": r.quantity,
                "unit_cost": r.unit_cost,
                "line_amount": r.line_amount,
                "payment_status": r.purchase.payment_status,
                "is_credit": r.purchase.is_credit,
            }
            for r in rows
        ]
        return Response(data)


class LedgerViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "inventory"
    queryset = InventoryLedger.objects.select_related("item")
    serializer_class = LedgerSerializer
    http_method_names = ["get", "head", "options"]


class SupplierViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "procurement"
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    search_fields = ["name"]

    def get_queryset(self):
        from django.db.models import DecimalField, F, Q, Sum, Value
        from django.db.models.functions import Coalesce, Greatest

        qs = super().get_queryset()
        # Sum of max(total - amount_paid, 0) for unpaid/partial POs (matches outstanding_balance).
        return qs.annotate(
            outstanding_total=Coalesce(
                Sum(
                    Greatest(
                        F("purchase_orders__total") - F("purchase_orders__amount_paid"),
                        Value(0),
                    ),
                    filter=(
                        ~Q(purchase_orders__status=PurchaseOrder.Status.CANCELLED)
                        & ~Q(purchase_orders__payment_status=PurchaseOrder.PaymentStatus.PAID)
                    ),
                ),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )


class PurchaseViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "procurement"
    queryset = PurchaseOrder.objects.select_related("supplier", "created_by").prefetch_related(
        "lines__item__unit", "payments"
    )
    serializer_class = PurchaseSerializer
    search_fields = ["number", "supplier__name"]

    def perform_create(self, serializer):
        restaurant = self.request.restaurant
        number = serializer.validated_data.get("number") or ""
        if not number:
            count = PurchaseOrder.objects.filter(restaurant=restaurant).count() + 1
            number = f"PO-{count:04d}"
        serializer.save(restaurant=restaurant, created_by=self.request.user, number=number)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        po = self.get_object()
        try:
            receive_purchase(po, user=request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        po.refresh_from_db()
        log_action(request, "purchase.received", po)
        return Response(PurchaseSerializer(po).data)

    @action(detail=True, methods=["post"])
    def settle(self, request, pk=None):
        """Partial or full payment against a credit / unpaid purchase."""
        po = self.get_object()
        try:
            amount = Decimal(str(request.data.get("amount")))
        except (InvalidOperation, TypeError):
            return Response({"detail": "Valid amount is required"}, status=400)
        paid_at = request.data.get("paid_at") or None
        if paid_at == "":
            paid_at = None
        try:
            payment = settle_purchase(
                po,
                amount=amount,
                method=request.data.get("method") or "cash",
                paid_at=paid_at,
                note=request.data.get("note") or "",
                reference=request.data.get("reference") or "",
                user=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        po.refresh_from_db()
        log_action(request, "purchase.settled", po, {"amount": str(payment.amount)})
        return Response(PurchaseSerializer(po).data)

    @action(detail=False, methods=["get"], url_path="payables")
    def payables(self, request):
        """Open credit / unpaid purchases with pending amounts."""
        qs = (
            self.get_queryset()
            .exclude(status=PurchaseOrder.Status.CANCELLED)
            .exclude(payment_status=PurchaseOrder.PaymentStatus.PAID)
            .order_by("due_date", "created_at")
        )
        data = PurchaseSerializer(qs, many=True).data
        total_pending = sum(Decimal(str(row.get("pending_amount") or 0)) for row in data)
        return Response({"results": data, "total_pending": total_pending})


class ReturnViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "inventory"
    queryset = StockReturn.objects.all()
    serializer_class = ReturnSerializer
