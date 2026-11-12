from django.db.models import Count, DecimalField, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.customers.models import CustomerAddress, RestaurantCustomer
from apps.orders.models import Order


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerAddress
        fields = ("id", "label", "line", "landmark", "is_default")


class CustomerSerializer(serializers.ModelSerializer):
    addresses = AddressSerializer(many=True, read_only=True)
    is_app_user = serializers.SerializerMethodField()
    orders_count = serializers.IntegerField(read_only=True, required=False)
    spend_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, required=False)
    last_order_at = serializers.DateTimeField(read_only=True, required=False, allow_null=True)
    delivery_completed_count = serializers.IntegerField(read_only=True, required=False)
    segment_flags = serializers.SerializerMethodField()

    class Meta:
        model = RestaurantCustomer
        fields = (
            "id",
            "user",
            "name",
            "phone",
            "email",
            "status",
            "source",
            "enquiry_note",
            "enquiry_at",
            "notes",
            "points_balance",
            "addresses",
            "is_app_user",
            "orders_count",
            "spend_total",
            "last_order_at",
            "delivery_completed_count",
            "segment_flags",
            "created_at",
        )
        read_only_fields = ("points_balance", "enquiry_at")

    def get_is_app_user(self, obj):
        return bool(obj.user_id) or obj.source == RestaurantCustomer.Source.APP

    def get_segment_flags(self, obj):
        delivery_done = (
            obj.source == RestaurantCustomer.Source.DELIVERY
            or int(getattr(obj, "delivery_completed_count", 0) or 0) > 0
        )
        return {
            "enquiry": obj.source == RestaurantCustomer.Source.ENQUIRY,
            "app_user": self.get_is_app_user(obj),
            "delivery_done": delivery_done,
        }

    def create(self, validated):
        if validated.get("source") == RestaurantCustomer.Source.ENQUIRY and not validated.get("enquiry_at"):
            validated["enquiry_at"] = timezone.now()
        return super().create(validated)

    def update(self, instance, validated):
        if (
            validated.get("source") == RestaurantCustomer.Source.ENQUIRY
            and instance.source != RestaurantCustomer.Source.ENQUIRY
            and not validated.get("enquiry_at")
            and not instance.enquiry_at
        ):
            validated["enquiry_at"] = timezone.now()
        return super().update(instance, validated)


class CustomerViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "customers"
    queryset = RestaurantCustomer.objects.prefetch_related("addresses")
    serializer_class = CustomerSerializer
    search_fields = ["name", "phone", "email", "enquiry_note"]

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .annotate(
                orders_count=Count("orders", distinct=True),
                spend_total=Coalesce(
                    Sum(
                        "orders__total",
                        filter=~Q(orders__status=Order.Status.CANCELLED),
                    ),
                    Value(0),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
                last_order_at=Max("orders__created_at"),
                delivery_completed_count=Count(
                    "orders",
                    filter=Q(
                        orders__order_type=Order.Type.DELIVERY,
                        orders__status=Order.Status.COMPLETED,
                    )
                    | Q(orders__delivery_job__status="delivered"),
                    distinct=True,
                ),
            )
        )
        segment = (self.request.query_params.get("segment") or "").strip().lower()
        source = (self.request.query_params.get("source") or "").strip().lower()
        if source:
            qs = qs.filter(source=source)
        if segment == "enquiries":
            qs = qs.filter(source=RestaurantCustomer.Source.ENQUIRY)
        elif segment == "app_users":
            qs = qs.filter(Q(user__isnull=False) | Q(source=RestaurantCustomer.Source.APP))
        elif segment == "delivery_done":
            qs = qs.filter(
                Q(source=RestaurantCustomer.Source.DELIVERY)
                | Q(orders__order_type=Order.Type.DELIVERY, orders__status=Order.Status.COMPLETED)
                | Q(orders__delivery_job__status="delivered")
            ).distinct()
        return qs

    @action(detail=False, methods=["get"])
    def summary(self, request):
        base = RestaurantCustomer.objects.filter(restaurant=request.restaurant, deleted_at__isnull=True)
        data = {
            "total": base.count(),
            "enquiries": base.filter(source=RestaurantCustomer.Source.ENQUIRY).count(),
            "app_users": base.filter(Q(user__isnull=False) | Q(source=RestaurantCustomer.Source.APP)).count(),
            "delivery_done": base.filter(
                Q(source=RestaurantCustomer.Source.DELIVERY)
                | Q(orders__order_type=Order.Type.DELIVERY, orders__status=Order.Status.COMPLETED)
                | Q(orders__delivery_job__status="delivered")
            )
            .distinct()
            .count(),
            "points_outstanding": base.aggregate(
                s=Coalesce(Sum("points_balance"), Value(0), output_field=DecimalField(max_digits=14, decimal_places=2))
            )["s"],
        }
        return Response(data)
