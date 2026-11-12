from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.expenses.defaults import ensure_default_expense_categories
from apps.expenses.models import Expense, ExpenseCategory


class CategorySerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True, allow_null=True)
    full_path = serializers.CharField(read_only=True)
    is_parent = serializers.BooleanField(read_only=True)
    children_count = serializers.SerializerMethodField()

    class Meta:
        model = ExpenseCategory
        fields = (
            "id",
            "name",
            "parent",
            "parent_name",
            "full_path",
            "is_parent",
            "sort_order",
            "is_active",
            "children_count",
        )

    def get_children_count(self, obj):
        # Prefer prefetched children (avoids N+1 .count() queries).
        cache = getattr(obj, "_prefetched_objects_cache", None)
        if cache is not None and "children" in cache:
            return sum(1 for c in obj.children.all() if c.is_active)
        if hasattr(obj, "active_children_count"):
            return int(obj.active_children_count or 0)
        return obj.children.filter(is_active=True).count()

    def validate_parent(self, parent):
        if parent is None:
            return parent
        request = self.context.get("request")
        restaurant = getattr(request, "restaurant", None) if request else None
        if restaurant and parent.restaurant_id != restaurant.id:
            raise serializers.ValidationError("Parent must belong to this restaurant")
        if parent.parent_id:
            raise serializers.ValidationError("Only two category levels are allowed")
        return parent

    def validate(self, attrs):
        parent = attrs.get("parent", getattr(self.instance, "parent", None))
        name = attrs.get("name", getattr(self.instance, "name", None))
        request = self.context.get("request")
        restaurant = getattr(request, "restaurant", None) if request else None
        if restaurant and name:
            qs = ExpenseCategory.objects.filter(restaurant=restaurant, parent=parent, name=name)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"name": "Category already exists under this parent"})
        return attrs


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_path = serializers.CharField(source="category.full_path", read_only=True)
    parent_category = serializers.UUIDField(source="category.parent_id", read_only=True, allow_null=True)
    parent_category_name = serializers.CharField(source="category.parent.name", read_only=True, allow_null=True)

    class Meta:
        model = Expense
        fields = (
            "id",
            "category",
            "category_name",
            "category_path",
            "parent_category",
            "parent_category_name",
            "payee",
            "amount",
            "date",
            "method",
            "note",
            "receipt_name",
            "is_recurring",
            "approved",
        )

    def validate_category(self, category):
        # Prefer leaf categories; allow parent only if it has no children.
        if category.children.filter(is_active=True).exists():
            raise serializers.ValidationError(
                "Pick a subcategory (level 2). Parent categories like Rent / Tax / Event are groups only."
            )
        return category

    def create(self, validated):
        obj = super().create(validated)
        log_action(self.context["request"], "expense.created", obj)
        return obj


class CategoryViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "expenses"
    queryset = ExpenseCategory.objects.select_related("parent").prefetch_related("children")
    serializer_class = CategorySerializer
    filterset_fields = ["parent", "is_active"]
    search_fields = ["name"]

    def get_queryset(self):
        from django.db.models import Count, Q

        qs = super().get_queryset().annotate(
            active_children_count=Count("children", filter=Q(children__is_active=True))
        )
        level = (self.request.query_params.get("level") or "").strip().lower()
        if level in ("1", "parent", "parents"):
            qs = qs.filter(parent__isnull=True)
        elif level in ("2", "child", "children", "leaf"):
            qs = qs.filter(parent__isnull=False)
        return qs

    @action(detail=False, methods=["post"])
    def seed_defaults(self, request):
        created = ensure_default_expense_categories(request.restaurant)
        return Response(
            {
                "created": len(created),
                "categories": CategorySerializer(
                    ExpenseCategory.objects.filter(restaurant=request.restaurant).select_related("parent"),
                    many=True,
                ).data,
            }
        )

    @action(detail=False, methods=["get"])
    def tree(self, request):
        parents = (
            ExpenseCategory.objects.filter(restaurant=request.restaurant, parent__isnull=True)
            .prefetch_related("children")
            .order_by("sort_order", "name")
        )
        data = []
        for p in parents:
            children = [c for c in p.children.all() if c.is_active]
            children.sort(key=lambda c: (c.sort_order, c.name))
            data.append(
                {
                    "id": str(p.id),
                    "name": p.name,
                    "is_active": p.is_active,
                    "sort_order": p.sort_order,
                    "children": [
                        {
                            "id": str(c.id),
                            "name": c.name,
                            "is_active": c.is_active,
                            "sort_order": c.sort_order,
                            "full_path": c.full_path,
                        }
                        for c in children
                    ],
                }
            )
        return Response(data)


class ExpenseViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "expenses"
    queryset = Expense.objects.select_related("category", "category__parent")
    serializer_class = ExpenseSerializer
    filterset_fields = ["category", "date", "approved", "category__parent"]
    search_fields = ["payee", "note", "category__name"]
