from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.inventory.views import (
    ItemViewSet,
    LedgerViewSet,
    PurchaseViewSet,
    ReturnViewSet,
    SupplierViewSet,
    UnitViewSet,
)

router = DefaultRouter()
router.register("units", UnitViewSet, basename="inv-units")
router.register("items", ItemViewSet, basename="inv-items")
router.register("movements", LedgerViewSet, basename="inv-movements")
router.register("suppliers", SupplierViewSet, basename="inv-suppliers")
router.register("purchases", PurchaseViewSet, basename="inv-purchases")
router.register("returns", ReturnViewSet, basename="inv-returns")
urlpatterns = [path("", include(router.urls))]
