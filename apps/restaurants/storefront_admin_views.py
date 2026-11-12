"""Shell endpoints: PWA subdomain, custom domain, API token management."""

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.services import enabled_module_codes
from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.restaurants.models import StorefrontAPIToken
from apps.restaurants.storefront_hosts import base_domain, subdomain_suffix


class StorefrontConfigView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "online"

    def get(self, request):
        r = request.restaurant
        modules = enabled_module_codes(r)
        return Response(
            {
                "slug": r.slug,
                "online_enabled": "online" in modules,
                "custom_storefront_enabled": "custom_storefront" in modules,
                "subdomain_suffix": subdomain_suffix(),
                "base_domain": base_domain(),
                "pwa_subdomain": r.pwa_subdomain,
                "pwa_host": r.pwa_host,
                "pwa_url": r.pwa_url,
                "custom_domain": r.custom_domain,
                "custom_domain_status": r.custom_domain_status,
                "pricing": {
                    "online": {
                        "code": "online",
                        "name": "Customer App (PWA)",
                        "includes": [
                            "Shared BiteMyBills customer template",
                            f"Dedicated installable PWA at {{slug}}{subdomain_suffix()}.{base_domain()}",
                            "Menu, orders, loyalty via storefront",
                        ],
                    },
                    "custom_storefront": {
                        "code": "custom_storefront",
                        "name": "Custom Website & API",
                        "includes": [
                            "Connect a manually designed website on your own domain",
                            "Token-based partner API for menu, orders and loyalty",
                            "DNS verification for custom domain",
                        ],
                    },
                },
            }
        )

    def patch(self, request):
        r = request.restaurant
        modules = enabled_module_codes(r)
        if "custom_domain" in request.data:
            if "custom_storefront" not in modules:
                return Response(
                    {"detail": "Enable Custom Website & API module to set a custom domain"},
                    status=403,
                )
            domain = (request.data.get("custom_domain") or "").strip().lower()
            r.custom_domain = domain
            if domain:
                if r.custom_domain_status == r.CustomDomainStatus.NONE:
                    r.custom_domain_status = r.CustomDomainStatus.PENDING
            else:
                r.custom_domain_status = r.CustomDomainStatus.NONE
            r.save(update_fields=["custom_domain", "custom_domain_status", "updated_at"])
            log_action(request, "storefront.custom_domain_updated", r, {"custom_domain": domain})
        return self.get(request)


class StorefrontAPITokenListCreateView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "custom_storefront"

    def get(self, request):
        if "custom_storefront" not in enabled_module_codes(request.restaurant):
            return Response({"detail": "Custom Website & API module required"}, status=403)
        tokens = StorefrontAPIToken.objects.filter(restaurant=request.restaurant, revoked_at__isnull=True)
        return Response(
            [
                {
                    "id": str(t.id),
                    "name": t.name,
                    "prefix": t.prefix,
                    "scopes": t.scopes,
                    "last_used_at": t.last_used_at,
                    "created_at": t.created_at,
                }
                for t in tokens
            ]
        )

    def post(self, request):
        if "custom_storefront" not in enabled_module_codes(request.restaurant):
            return Response({"detail": "Custom Website & API module required"}, status=403)
        name = (request.data.get("name") or "Custom website").strip()
        scopes = request.data.get("scopes") or ["menu", "orders", "loyalty", "restaurant"]
        token, raw = StorefrontAPIToken.issue(
            restaurant=request.restaurant,
            name=name,
            scopes=scopes,
            created_by=request.user,
        )
        log_action(request, "storefront.api_token_created", token, {"name": name})
        return Response(
            {
                "id": str(token.id),
                "name": token.name,
                "prefix": token.prefix,
                "scopes": token.scopes,
                "token": raw,  # shown once
                "created_at": token.created_at,
            },
            status=201,
        )


class StorefrontAPITokenRevokeView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "custom_storefront"
    permission_action = "Delete"

    def post(self, request, pk):
        token = StorefrontAPIToken.objects.filter(restaurant=request.restaurant, pk=pk, revoked_at__isnull=True).first()
        if not token:
            return Response({"detail": "Token not found"}, status=404)
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at", "updated_at"])
        log_action(request, "storefront.api_token_revoked", token)
        return Response({"id": str(token.id), "revoked": True})
