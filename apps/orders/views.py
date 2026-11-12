from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.common.realtime import publish
from apps.common.viewsets import TenantModelViewSet
from apps.orders.models import DiningTable, KitchenTicket, Order, OrderItem, OrderItemModifier, TableSession
from apps.orders.services import create_order, send_kot


class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiningTable
        fields = ("id", "code", "zone", "seats", "status", "join_code")


class SessionSerializer(serializers.ModelSerializer):
    table_code = serializers.CharField(source="table.code", read_only=True)

    class Meta:
        model = TableSession
        fields = (
            "id",
            "table",
            "table_code",
            "customer",
            "guest_count",
            "status",
            "opened_at",
            "closed_at",
        )


class ModifierInSerializer(serializers.Serializer):
    name = serializers.CharField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2)


class OrderItemInSerializer(serializers.Serializer):
    menu_item = serializers.UUIDField(required=False)
    name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    notes = serializers.CharField(required=False, allow_blank=True)
    station = serializers.CharField(required=False, allow_blank=True)
    modifiers = ModifierInSerializer(many=True, required=False)


class OrderWriteSerializer(serializers.Serializer):
    order_type = serializers.ChoiceField(choices=Order.Type.choices)
    channel = serializers.ChoiceField(choices=Order.Channel.choices, required=False)
    session = serializers.UUIDField(required=False)
    table = serializers.UUIDField(required=False)
    customer = serializers.UUIDField(required=False)
    customer_name = serializers.CharField(required=False, allow_blank=True)
    customer_phone = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    delivery_fee = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    packaging_fee = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    payment_mode = serializers.CharField(required=False, allow_blank=True)
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    items = OrderItemInSerializer(many=True)


class OrderItemOutSerializer(serializers.ModelSerializer):
    modifiers = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ("id", "name", "quantity", "unit_price", "notes", "station", "kitchen_status", "modifiers")

    def get_modifiers(self, obj):
        return [{"name": m.name, "price": m.price} for m in obj.modifiers.all()]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemOutSerializer(many=True, read_only=True)
    table_code = serializers.CharField(source="table.code", read_only=True, default="")

    class Meta:
        model = Order
        fields = (
            "id",
            "number",
            "order_type",
            "channel",
            "status",
            "payment_status",
            "session",
            "table",
            "table_code",
            "customer",
            "customer_name",
            "customer_phone",
            "notes",
            "subtotal",
            "discount",
            "tax",
            "delivery_fee",
            "packaging_fee",
            "total",
            "payment_mode",
            "placed_at",
            "items",
        )


class KitchenTicketSerializer(serializers.ModelSerializer):
    number = serializers.CharField(source="order.number", read_only=True)
    order_type = serializers.CharField(source="order.order_type", read_only=True)
    lines = serializers.SerializerMethodField()

    class Meta:
        model = KitchenTicket
        fields = ("id", "order", "number", "order_type", "stage", "priority", "sla_minutes", "note", "created_at", "lines")

    def get_lines(self, obj):
        return [
            {
                "id": str(l.id),
                "name": l.name,
                "qty": l.quantity,
                "station": l.station,
                "instructions": l.instructions,
                "stage": l.stage,
            }
            for l in obj.lines.all()
        ]


class TableViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "tables"
    queryset = DiningTable.objects.all()
    serializer_class = TableSerializer
    search_fields = ["code", "zone"]


class SessionViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "tables"
    queryset = TableSession.objects.select_related("table")
    serializer_class = SessionSerializer


class OrderViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "orders"
    queryset = Order.objects.prefetch_related("items__modifiers").select_related("table")
    serializer_class = OrderSerializer
    filterset_fields = ["order_type", "status", "payment_status", "channel"]
    search_fields = ["number", "customer_name", "customer_phone"]

    def create(self, request, *args, **kwargs):
        ser = OrderWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        order = create_order(restaurant=request.restaurant, user=request.user, data=ser.validated_data)
        log_action(request, "order.created", order, {"number": order.number})
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def send_kot(self, request, pk=None):
        order = self.get_object()
        ticket = send_kot(order, request.user)
        return Response(KitchenTicketSerializer(ticket).data)

    @action(detail=True, methods=["post"])
    def collect_payment(self, request, pk=None):
        order = self.get_object()
        order.payment_status = Order.PaymentStatus.PAID
        order.payment_mode = request.data.get("payment_mode") or order.payment_mode
        if order.status not in (Order.Status.CANCELLED, Order.Status.COMPLETED):
            if order.order_type == Order.Type.DINE_IN:
                order.status = Order.Status.COMPLETED
            elif order.status in (Order.Status.READY, Order.Status.PICKED_UP, Order.Status.SERVED):
                order.status = Order.Status.COMPLETED
        order.save()
        from apps.orders.services import maybe_earn_loyalty

        maybe_earn_loyalty(order)
        publish(f"order_{order.id}", "order.updated", {"status": order.status, "payment_status": order.payment_status})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        order = self.get_object()
        status_value = request.data.get("status")
        if status_value not in dict(Order.Status.choices):
            return Response({"detail": "Invalid status"}, status=400)
        order.status = status_value
        order.save(update_fields=["status", "updated_at"])
        if status_value == Order.Status.COMPLETED:
            from apps.orders.services import maybe_earn_loyalty

            maybe_earn_loyalty(order)
            # Mark linked delivery job delivered when order completes
            job = getattr(order, "delivery_job", None)
            if job and job.status != "delivered":
                from apps.delivery.models import DeliveryJob

                job.status = DeliveryJob.Status.DELIVERED
                job.save(update_fields=["status", "updated_at"])
        publish(f"order_{order.id}", "order.updated", {"status": order.status})
        publish(f"orders_{order.restaurant_id}", "order.updated", {"id": str(order.id), "status": order.status})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        log_action(request, "order.cancelled", order)
        publish(f"order_{order.id}", "order.updated", {"status": order.status})
        return Response(OrderSerializer(order).data)


class KitchenViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "kitchen"
    queryset = KitchenTicket.objects.select_related("order").prefetch_related("lines")
    serializer_class = KitchenTicketSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def partial_update(self, request, *args, **kwargs):
        ticket = self.get_object()
        stage = request.data.get("stage")
        if stage:
            ticket.stage = stage
            ticket.save(update_fields=["stage", "updated_at"])
            if stage == "preparing":
                ticket.order.status = Order.Status.PREPARING
            elif stage == "ready":
                ticket.order.status = Order.Status.READY
            elif stage == "completed":
                if ticket.order.order_type == Order.Type.DINE_IN:
                    ticket.order.status = Order.Status.SERVED
            ticket.order.save(update_fields=["status", "updated_at"])
            publish(f"kitchen_{ticket.restaurant_id}", "kitchen.updated", {"id": str(ticket.id), "stage": stage})
            publish(f"order_{ticket.order_id}", "order.updated", {"status": ticket.order.status})
        return Response(KitchenTicketSerializer(ticket).data)
