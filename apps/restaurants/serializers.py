from rest_framework import serializers

from apps.billing.models import CatalogModule, RestaurantModule
from apps.restaurants.models import Restaurant, RestaurantMembership, RestaurantSettings, Role


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = RestaurantSettings
        exclude = ("id", "restaurant", "created_at", "updated_at")


class RestaurantSerializer(serializers.ModelSerializer):
    settings = SettingsSerializer(read_only=True)
    modules = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    pwa_url = serializers.CharField(read_only=True)
    pwa_host = serializers.CharField(read_only=True)

    class Meta:
        model = Restaurant
        fields = (
            "id",
            "name",
            "slug",
            "owner_name",
            "email",
            "phone",
            "restaurant_type",
            "address",
            "city",
            "branch",
            "location",
            "initials",
            "currency",
            "timezone",
            "order_prefix",
            "theme",
            "description",
            "custom_domain",
            "custom_domain_status",
            "pwa_url",
            "pwa_host",
            "account_status",
            "grace_months_used",
            "outstanding_amount",
            "is_archived",
            "settings",
            "modules",
            "role",
        )
        read_only_fields = (
            "account_status",
            "grace_months_used",
            "outstanding_amount",
            "slug",
            "custom_domain_status",
            "pwa_url",
            "pwa_host",
        )

    def get_modules(self, obj):
        cache = getattr(obj, "_prefetched_objects_cache", None)
        if cache is not None and "enabled_modules" in cache:
            return [
                link.module.code
                for link in obj.enabled_modules.all()
                if link.enabled and getattr(link, "module", None) is not None
            ]
        return list(
            RestaurantModule.objects.filter(restaurant=obj, enabled=True).values_list("module__code", flat=True)
        )

    def get_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        cache = getattr(obj, "_prefetched_objects_cache", None)
        if cache is not None and "memberships" in cache:
            for mem in obj.memberships.all():
                if mem.user_id == request.user.id and mem.is_active:
                    return mem.role.code if mem.role_id else None
            return None
        mem = obj.memberships.filter(user=request.user, is_active=True).select_related("role").first()
        return mem.role.code if mem else None


class MembershipWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = RestaurantMembership
        fields = ("id", "user", "role", "is_active")


class MembershipReadSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email")
    name = serializers.CharField(source="user.name")
    role_code = serializers.CharField(source="role.code")
    role_name = serializers.CharField(source="role.name")

    class Meta:
        model = RestaurantMembership
        fields = ("id", "user", "email", "name", "role", "role_code", "role_name", "is_active")


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ("id", "code", "name", "description", "permissions")

    def get_permissions(self, obj):
        return [f"{p.scope}:{p.action}" for p in obj.permissions.all()]


class CatalogModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogModule
        fields = ("id", "code", "name", "group", "description", "monthly_price", "is_active", "aliases")
