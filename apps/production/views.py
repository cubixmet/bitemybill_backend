from decimal import Decimal

from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.inventory.services import convert
from apps.production.models import ProductionBatch, Recipe, RecipeIngredient
from apps.production.services import complete_batch, record_balance_waste

COST_WRITE_FIELDS = (
    "electricity_cost",
    "gas_fuel_cost",
    "labor_cost",
    "selling_price",
)


def _membership_can(request, scope: str, action: str) -> bool:
    user = request.user
    if getattr(user, "user_kind", None) == "platform":
        return True
    membership = getattr(request, "membership", None)
    if not membership:
        return False
    return membership.has_permission(scope, action)


def _line_ingredient_cost(ing: RecipeIngredient) -> Decimal:
    item = ing.inventory_item
    qty = convert(Decimal(ing.quantity), ing.unit_code, item.unit)
    return (qty * Decimal(item.cost or 0)).quantize(Decimal("0.01"))


class IngredientSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="inventory_item.name", read_only=True)
    item_unit_cost = serializers.DecimalField(
        source="inventory_item.cost", max_digits=12, decimal_places=2, read_only=True
    )
    resolved_wastage = serializers.SerializerMethodField()
    estimated_cost = serializers.SerializerMethodField()

    class Meta:
        model = RecipeIngredient
        fields = (
            "id",
            "inventory_item",
            "item_name",
            "item_unit_cost",
            "quantity",
            "unit_code",
            "wastage_qty",
            "wastage_pct",
            "wastage_note",
            "resolved_wastage",
            "estimated_cost",
            "note",
        )

    def get_resolved_wastage(self, obj):
        return obj.resolved_wastage()

    def get_estimated_cost(self, obj):
        return _line_ingredient_cost(obj)


class RecipeSerializer(serializers.ModelSerializer):
    ingredients = IngredientSerializer(many=True, required=False)
    output_item_name = serializers.CharField(source="output_item.name", read_only=True, default=None)
    ingredient_cost = serializers.SerializerMethodField()
    overhead_cost = serializers.SerializerMethodField()
    total_cost = serializers.SerializerMethodField()
    cost_per_unit = serializers.SerializerMethodField()
    profit_amount = serializers.SerializerMethodField()
    profit_margin_pct = serializers.SerializerMethodField()
    can_view_costs = serializers.SerializerMethodField()
    can_edit_costs = serializers.SerializerMethodField()

    class Meta:
        model = Recipe
        fields = (
            "id",
            "name",
            "category",
            "yield_qty",
            "yield_unit",
            "batch_time_mins",
            "station",
            "steps",
            "prep_type",
            "output_item",
            "output_item_name",
            "electricity_cost",
            "gas_fuel_cost",
            "labor_cost",
            "selling_price",
            "ingredient_cost",
            "overhead_cost",
            "total_cost",
            "cost_per_unit",
            "profit_amount",
            "profit_margin_pct",
            "can_view_costs",
            "can_edit_costs",
            "ingredients",
        )

    def _can_view(self):
        request = self.context.get("request")
        if not request:
            return False
        return _membership_can(request, "recipe_costs", "View")

    def _can_edit(self):
        request = self.context.get("request")
        if not request:
            return False
        return _membership_can(request, "recipe_costs", "Edit")

    def get_can_view_costs(self, obj):
        return self._can_view()

    def get_can_edit_costs(self, obj):
        return self._can_edit()

    def get_ingredient_cost(self, obj):
        if not self._can_view():
            return None
        return obj.ingredient_cost()

    def get_overhead_cost(self, obj):
        if not self._can_view():
            return None
        return obj.overhead_cost()

    def get_total_cost(self, obj):
        if not self._can_view():
            return None
        return obj.total_cost()

    def get_cost_per_unit(self, obj):
        if not self._can_view():
            return None
        yq = Decimal(obj.yield_qty or 0)
        if yq <= 0:
            return None
        return (obj.total_cost() / yq).quantize(Decimal("0.01"))

    def get_profit_amount(self, obj):
        if not self._can_view():
            return None
        return obj.profit_amount()

    def get_profit_margin_pct(self, obj):
        if not self._can_view():
            return None
        sell = Decimal(obj.selling_price or 0)
        if sell <= 0:
            return None
        profit = obj.profit_amount()
        if profit is None:
            return None
        return ((profit / sell) * Decimal("100")).quantize(Decimal("0.01"))

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._can_view():
            for key in (
                "electricity_cost",
                "gas_fuel_cost",
                "labor_cost",
                "selling_price",
                "ingredient_cost",
                "overhead_cost",
                "total_cost",
                "cost_per_unit",
                "profit_amount",
                "profit_margin_pct",
            ):
                data[key] = None
            # Strip per-line money from ingredients for kitchen / other roles
            for row in data.get("ingredients") or []:
                row["estimated_cost"] = None
                row["item_unit_cost"] = None
        return data

    def validate(self, attrs):
        request = self.context.get("request")
        touching_costs = any(f in attrs for f in COST_WRITE_FIELDS)
        if touching_costs and request and not _membership_can(request, "recipe_costs", "Edit"):
            raise serializers.ValidationError(
                {"detail": "Your role cannot edit recipe overhead costs or selling price (recipe_costs:Edit required)."}
            )
        return attrs

    def _sync_ingredients(self, recipe, ings):
        recipe.ingredients.all().delete()
        for row in ings:
            RecipeIngredient.objects.create(recipe=recipe, **row)

    def create(self, validated):
        ings = validated.pop("ingredients", [])
        recipe = Recipe.objects.create(**validated)
        self._sync_ingredients(recipe, ings)
        return recipe

    def update(self, instance, validated):
        ings = validated.pop("ingredients", None)
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()
        if ings is not None:
            self._sync_ingredients(instance, ings)
        return instance


class BatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionBatch
        fields = (
            "id",
            "recipe",
            "product_name",
            "unit",
            "planned_qty",
            "actual_qty",
            "wastage_qty",
            "status",
            "shift",
            "staff_name",
            "note",
            "posted",
            "created_at",
        )


class RecipeViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "recipes"
    queryset = Recipe.objects.select_related("output_item").prefetch_related(
        "ingredients__inventory_item", "ingredients__inventory_item__unit"
    )
    serializer_class = RecipeSerializer
    search_fields = ["name"]

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx


class BatchViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "production"
    queryset = ProductionBatch.objects.select_related("recipe")
    serializer_class = BatchSerializer

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        batch = self.get_object()
        try:
            complete_batch(
                batch,
                actual=request.data.get("actual_qty"),
                wastage=request.data.get("wastage_qty"),
                user=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        log_action(request, "production.recorded", batch)
        return Response(BatchSerializer(batch).data)

    @action(detail=True, methods=["post"], url_path="balance-waste")
    def balance_waste(self, request, pk=None):
        """Write off leftover / unsold finished food after service."""
        batch = self.get_object()
        qty = request.data.get("quantity")
        if qty is None:
            qty = request.data.get("wastage_qty")
        try:
            record_balance_waste(
                batch,
                quantity=qty,
                user=request.user,
                note=request.data.get("note") or "",
            )
        except (TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=400)
        log_action(request, "production.balance_waste", batch)
        return Response(BatchSerializer(batch).data)
