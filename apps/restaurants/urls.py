from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.restaurants.storefront_admin_views import (
    StorefrontAPITokenListCreateView,
    StorefrontAPITokenRevokeView,
    StorefrontConfigView,
)
from apps.restaurants.views import (
    MembershipViewSet,
    ModuleToggleView,
    MyRestaurantsView,
    RestaurantDetailView,
    RestaurantSettingsView,
    RoleListView,
)

router = DefaultRouter()
router.register("memberships", MembershipViewSet, basename="memberships")

urlpatterns = [
    path("", MyRestaurantsView.as_view()),
    path("<uuid:pk>/", RestaurantDetailView.as_view()),
    path("current/settings/", RestaurantSettingsView.as_view()),
    path("current/modules/", ModuleToggleView.as_view()),
    path("current/storefront/", StorefrontConfigView.as_view()),
    path("current/storefront/tokens/", StorefrontAPITokenListCreateView.as_view()),
    path("current/storefront/tokens/<uuid:pk>/revoke/", StorefrontAPITokenRevokeView.as_view()),
    path("roles/", RoleListView.as_view()),
    path("", include(router.urls)),
]
