from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.serializers import (
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
    UserSerializer,
    issue_tokens,
    make_reset_token,
    user_from_uid,
)
from apps.billing.services import provision_restaurant
from apps.common.audit import log_action


class LoginThrottle(SimpleRateThrottle):
    scope = "login"

    def get_cache_key(self, request, view):
        ident = request.data.get("identifier") or request.META.get("REMOTE_ADDR")
        return self.cache_format % {"scope": self.scope, "ident": ident}


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(
            email=data["email"],
            password=data["password"],
            display_name=data.get("display_name") or data.get("owner_name") or "",
            phone=data.get("phone") or "",
            user_kind="staff",
        )
        restaurant = provision_restaurant(
            owner=user,
            name=data["restaurant_name"],
            restaurant_type=data.get("restaurant_type") or "",
            address=data.get("address") or "",
            owner_name=data.get("owner_name") or user.name,
            email=user.email,
            phone=user.phone,
        )
        log_action(request, "restaurant.created", restaurant, {"name": restaurant.name})
        return Response(issue_tokens(user), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.validated_data["user"]
        location = self._enforce_location(user, ser.validated_data)
        payload = issue_tokens(user)
        if location is not None:
            payload["location"] = location
            log_action(
                request,
                "auth.login_location",
                user,
                location,
            )
        return Response(payload)

    def _enforce_location(self, user, data):
        """
        When a membership restaurant requires location login, non-owner staff must
        be inside that restaurant's geofence. Owners / platform admins bypass.
        """
        from apps.restaurants.models import RestaurantMembership, RestaurantSettings
        from apps.staff.services import haversine_m

        memberships = (
            RestaurantMembership.objects.filter(user=user, is_active=True)
            .select_related("restaurant", "role", "restaurant__settings")
        )
        restaurant_id = data.get("restaurant_id")
        if restaurant_id:
            memberships = memberships.filter(restaurant_id=restaurant_id)

        gated = []
        for mem in memberships:
            if mem.role.code in ("owner", "platform_admin", "admin"):
                continue
            settings_obj = getattr(mem.restaurant, "settings", None)
            if not settings_obj:
                settings_obj, _ = RestaurantSettings.objects.get_or_create(restaurant=mem.restaurant)
            if settings_obj.require_location_login:
                gated.append((mem, settings_obj))

        if not gated:
            lat, lng = data.get("latitude"), data.get("longitude")
            if lat is not None and lng is not None:
                return {"latitude": lat, "longitude": lng, "required": False, "ok": True}
            return None

        lat, lng = data.get("latitude"), data.get("longitude")
        if lat is None or lng is None:
            from rest_framework.exceptions import ValidationError

            raise ValidationError(
                {"detail": "Location is required to sign in for this restaurant. Enable GPS and try again."}
            )

        nearest = None
        for mem, settings_obj in gated:
            if settings_obj.latitude is None or settings_obj.longitude is None:
                # Misconfigured restaurant — allow login but flag it
                nearest = {
                    "restaurant_id": str(mem.restaurant_id),
                    "restaurant": mem.restaurant.name,
                    "ok": True,
                    "required": True,
                    "configured": False,
                    "latitude": lat,
                    "longitude": lng,
                }
                continue
            distance = round(
                haversine_m(
                    float(lat),
                    float(lng),
                    float(settings_obj.latitude),
                    float(settings_obj.longitude),
                ),
                1,
            )
            ok = distance <= settings_obj.login_radius_m
            candidate = {
                "restaurant_id": str(mem.restaurant_id),
                "restaurant": mem.restaurant.name,
                "ok": ok,
                "required": True,
                "configured": True,
                "distance_m": distance,
                "radius_m": settings_obj.login_radius_m,
                "latitude": lat,
                "longitude": lng,
            }
            if ok:
                return candidate
            if nearest is None or distance < nearest.get("distance_m", 1e12):
                nearest = candidate

        from rest_framework.exceptions import PermissionDenied

        dist = nearest.get("distance_m") if nearest else None
        radius = nearest.get("radius_m") if nearest else None
        raise PermissionDenied(
            f"You must be at the restaurant to sign in"
            + (f" ({dist}m away, allowed {radius}m)" if dist is not None else "")
            + "."
        )


class LogoutView(APIView):
    def post(self, request):
        token = request.data.get("refresh")
        if token:
            try:
                RefreshToken(token).blacklist()
            except Exception:
                pass
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if not request.user.check_password(ser.validated_data["old_password"]):
            return Response({"detail": "Wrong password"}, status=400)
        request.user.set_password(ser.validated_data["new_password"])
        request.user.save()
        return Response({"ok": True})


class ForgotPasswordView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = ForgotPasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.filter(email__iexact=ser.validated_data["email"]).first()
        if user:
            uid, token = make_reset_token(user)
            send_mail(
                "Reset your BiteMyBills password",
                f"Use uid={uid} token={token} to reset your password.",
                "noreply@bitemybills.tech",
                [user.email],
            )
        return Response({"ok": True})


class ResetPasswordView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = ResetPasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            user = user_from_uid(ser.validated_data["uid"])
        except Exception:
            return Response({"detail": "Invalid token"}, status=400)
        if not default_token_generator.check_token(user, ser.validated_data["token"]):
            return Response({"detail": "Invalid token"}, status=400)
        validate_password(ser.validated_data["new_password"])
        user.set_password(ser.validated_data["new_password"])
        user.save()
        return Response({"ok": True})
