from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.billing.models import RestaurantModule
from apps.restaurants.models import RestaurantMembership

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "phone", "display_name", "name", "user_kind", "first_name", "last_name")


class MembershipSerializer(serializers.ModelSerializer):
    restaurant_id = serializers.UUIDField(source="restaurant.id")
    restaurant_name = serializers.CharField(source="restaurant.name")
    slug = serializers.CharField(source="restaurant.slug")
    role = serializers.CharField(source="role.code")
    role_name = serializers.CharField(source="role.name")
    theme = serializers.CharField(source="restaurant.theme")
    account_status = serializers.CharField(source="restaurant.account_status")
    branch = serializers.CharField(source="restaurant.branch")
    initials = serializers.CharField(source="restaurant.initials")
    modules = serializers.SerializerMethodField()

    class Meta:
        model = RestaurantMembership
        fields = (
            "id",
            "restaurant_id",
            "restaurant_name",
            "slug",
            "role",
            "role_name",
            "theme",
            "account_status",
            "branch",
            "initials",
            "modules",
            "is_active",
        )

    def get_modules(self, obj):
        restaurant = obj.restaurant
        cache = getattr(restaurant, "_prefetched_objects_cache", None)
        if cache is not None and "enabled_modules" in cache:
            return [
                link.module.code
                for link in restaurant.enabled_modules.all()
                if link.enabled and getattr(link, "module", None) is not None
            ]
        return list(
            RestaurantModule.objects.filter(restaurant=restaurant, enabled=True).values_list(
                "module__code", flat=True
            )
        )


def issue_tokens(user):
    from django.db.models import Prefetch

    refresh = RefreshToken.for_user(user)
    refresh["user_kind"] = user.user_kind
    memberships = (
        user.memberships.filter(is_active=True)
        .select_related("restaurant", "role")
        .prefetch_related(
            Prefetch(
                "restaurant__enabled_modules",
                queryset=RestaurantModule.objects.filter(enabled=True).select_related("module"),
            )
        )
    )
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserSerializer(user).data,
        "memberships": MembershipSerializer(memberships, many=True).data,
    }


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=6, write_only=True)
    display_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    restaurant_name = serializers.CharField()
    restaurant_type = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    owner_name = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already registered")
        return value.lower()

    def validate_password(self, value):
        validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField()
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    restaurant_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        ident = attrs["identifier"].strip()
        user = User.objects.filter(email__iexact=ident).first()
        if not user:
            user = User.objects.filter(phone=ident).first()
        if not user or not user.check_password(attrs["password"]):
            raise serializers.ValidationError("Invalid credentials")
        if not user.is_active:
            raise serializers.ValidationError("Account disabled")
        attrs["user"] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField(min_length=6)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=6)


def make_reset_token(user):
    return urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)


def user_from_uid(uid):
    return User.objects.get(pk=force_str(urlsafe_base64_decode(uid)))
