from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.services import provision_restaurant
from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantAccess, HasRestaurantPermission, IsPlatformAdmin
from apps.restaurants.models import Restaurant, RestaurantMembership, RestaurantSettings, Role
from apps.restaurants.serializers import (
    CatalogModuleSerializer,
    MembershipReadSerializer,
    MembershipWriteSerializer,
    RestaurantSerializer,
    RoleSerializer,
    SettingsSerializer,
)
from apps.billing.models import CatalogModule, RestaurantModule

User = get_user_model()


def _restaurant_queryset_for_serialize():
    return Restaurant.objects.select_related("settings").prefetch_related(
        Prefetch(
            "enabled_modules",
            queryset=RestaurantModule.objects.filter(enabled=True).select_related("module"),
        ),
        Prefetch(
            "memberships",
            queryset=RestaurantMembership.objects.filter(is_active=True).select_related("role", "user"),
        ),
    )


class MyRestaurantsView(APIView):
    def get(self, request):
        restaurants = (
            _restaurant_queryset_for_serialize()
            .filter(memberships__user=request.user, memberships__is_active=True)
            .distinct()
        )
        return Response(RestaurantSerializer(restaurants, many=True, context={"request": request}).data)

    def post(self, request):
        name = request.data.get("name")
        if not name:
            return Response({"detail": "name required"}, status=400)
        restaurant = provision_restaurant(
            owner=request.user,
            name=name,
            restaurant_type=request.data.get("restaurant_type") or "",
            address=request.data.get("address") or "",
            owner_name=request.data.get("owner_name") or request.user.name,
            email=request.data.get("email") or request.user.email,
            phone=request.data.get("phone") or request.user.phone,
            modules=request.data.get("modules"),
        )
        log_action(request, "restaurant.created", restaurant)
        restaurant = _restaurant_queryset_for_serialize().filter(pk=restaurant.pk).first()
        return Response(RestaurantSerializer(restaurant, context={"request": request}).data, status=201)


class RestaurantDetailView(APIView):
    permission_classes = [HasRestaurantAccess]

    def get(self, request, pk):
        restaurant = _restaurant_queryset_for_serialize().filter(pk=pk).first()
        if not restaurant:
            return Response(status=404)
        if request.user.user_kind != "platform":
            if not RestaurantMembership.objects.filter(user=request.user, restaurant=restaurant, is_active=True).exists():
                return Response(status=403)
        return Response(RestaurantSerializer(restaurant, context={"request": request}).data)

    def patch(self, request, pk):
        restaurant = Restaurant.objects.filter(pk=pk).first()
        if not restaurant:
            return Response(status=404)
        ser = RestaurantSerializer(restaurant, data=request.data, partial=True, context={"request": request})
        ser.is_valid(raise_exception=True)
        ser.save()
        log_action(request, "restaurant.settings_changed", restaurant, request.data)
        return Response(ser.data)


class RestaurantSettingsView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "settings"

    def get(self, request):
        settings_obj, _ = RestaurantSettings.objects.get_or_create(restaurant=request.restaurant)
        return Response(SettingsSerializer(settings_obj).data)

    def patch(self, request):
        settings_obj, _ = RestaurantSettings.objects.get_or_create(restaurant=request.restaurant)
        ser = SettingsSerializer(settings_obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        if "theme" in request.data:
            request.restaurant.theme = request.data["theme"]
            request.restaurant.save(update_fields=["theme"])
        log_action(request, "restaurant.settings_changed", settings_obj, request.data)
        return Response(SettingsSerializer(settings_obj).data)


class MembershipViewSet(viewsets.ModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "users"
    serializer_class = MembershipReadSerializer

    def get_queryset(self):
        if not getattr(self.request, "restaurant", None):
            return RestaurantMembership.objects.none()
        return RestaurantMembership.objects.filter(restaurant=self.request.restaurant).select_related("user", "role")

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return MembershipWriteSerializer
        return MembershipReadSerializer

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.restaurant)
        log_action(self.request, "membership.created", serializer.instance)


class RoleListView(APIView):
    def get(self, request):
        return Response(RoleSerializer(Role.objects.prefetch_related("permissions").all(), many=True).data)


class ModuleToggleView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "subscription"
    # GET uses View; mutating methods use Edit (toggle is not Create).
    permission_action = None

    def get_permissions(self):
        self.permission_action = "Edit" if self.request.method not in ("GET", "HEAD", "OPTIONS") else None
        return super().get_permissions()

    def get(self, request):
        enabled = list(
            RestaurantModule.objects.filter(restaurant=request.restaurant, enabled=True).values_list(
                "module__code", flat=True
            )
        )
        catalog = CatalogModuleSerializer(CatalogModule.objects.filter(is_active=True), many=True).data
        return Response({"enabled": enabled, "catalog": catalog})

    def post(self, request):
        codes = request.data.get("modules") or []
        RestaurantModule.objects.filter(restaurant=request.restaurant).update(enabled=False)
        for code in codes:
            mod = CatalogModule.objects.filter(code=code).first()
            if not mod:
                continue
            link, _ = RestaurantModule.objects.get_or_create(restaurant=request.restaurant, module=mod)
            link.enabled = True
            link.save(update_fields=["enabled"])
        log_action(request, "modules.updated", request.restaurant, {"modules": codes})
        return self.get(request)
