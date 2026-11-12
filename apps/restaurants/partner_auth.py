from django.utils import timezone
from rest_framework import authentication, exceptions
from rest_framework.permissions import BasePermission

from apps.billing.services import enabled_module_codes
from apps.restaurants.models import StorefrontAPIToken


class StorefrontAPITokenAuthentication(authentication.BaseAuthentication):
    """Authenticate custom websites with `Authorization: Bearer bmb_sf_...`."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("utf-8")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            return None
        raw = parts[1].strip()
        if not raw.startswith("bmb_sf_"):
            return None
        token = (
            StorefrontAPIToken.objects.select_related("restaurant")
            .filter(token_hash=StorefrontAPIToken.hash_token(raw), revoked_at__isnull=True)
            .first()
        )
        if not token:
            raise exceptions.AuthenticationFailed("Invalid storefront API token")
        restaurant = token.restaurant
        if not restaurant.is_operational:
            raise exceptions.AuthenticationFailed("Restaurant is not operational")
        if "custom_storefront" not in enabled_module_codes(restaurant):
            raise exceptions.AuthenticationFailed("Custom Website & API module is not enabled")
        StorefrontAPIToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        request.storefront_token = token
        request.restaurant = restaurant
        # Token binds the restaurant; no staff user.
        return (None, token)


class IsStorefrontAPIToken(BasePermission):
    def has_permission(self, request, view):
        return bool(getattr(request, "storefront_token", None) and getattr(request, "restaurant", None))


class HasStorefrontScope(BasePermission):
    def has_permission(self, request, view):
        token = getattr(request, "storefront_token", None)
        if not token:
            return False
        needed = getattr(view, "partner_scope", "restaurant")
        scopes = token.scopes or []
        return needed in scopes or "*" in scopes
