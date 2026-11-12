from django.db.models import Prefetch
from rest_framework import serializers

from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.menu.models import MenuCategory, MenuItem, MenuModifierGroup, MenuModifierOption


class ModifierOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MenuModifierOption
        fields = ("id", "name", "price")


class ModifierGroupSerializer(serializers.ModelSerializer):
    options = ModifierOptionSerializer(many=True, read_only=True)

    class Meta:
        model = MenuModifierGroup
        fields = ("id", "name", "required", "min_select", "max_select", "options")


class MenuItemSerializer(serializers.ModelSerializer):
    modifier_groups = ModifierGroupSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = MenuItem
        fields = (
            "id",
            "category",
            "category_name",
            "name",
            "description",
            "price",
            "cost",
            "is_available",
            "is_veg",
            "prep_minutes",
            "station",
            "image_url",
            "tags",
            "points",
            "modifier_groups",
        )


class MenuCategorySerializer(serializers.ModelSerializer):
    items = MenuItemSerializer(many=True, read_only=True)

    class Meta:
        model = MenuCategory
        fields = ("id", "name", "slug", "sort_order", "is_active", "items")


class CategoryViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "menu"
    queryset = MenuCategory.objects.prefetch_related(
        Prefetch(
            "items",
            queryset=MenuItem.objects.select_related("category").prefetch_related(
                Prefetch(
                    "modifier_groups",
                    queryset=MenuModifierGroup.objects.prefetch_related("options"),
                )
            ),
        )
    )
    serializer_class = MenuCategorySerializer
    search_fields = ["name"]


class MenuItemViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "menu"
    queryset = MenuItem.objects.select_related("category").prefetch_related("modifier_groups__options")
    serializer_class = MenuItemSerializer
    filterset_fields = ["category", "is_available"]
    search_fields = ["name"]
