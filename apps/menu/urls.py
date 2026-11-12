from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.menu.views import CategoryViewSet, MenuItemViewSet

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="menu-categories")
router.register("items", MenuItemViewSet, basename="menu-items")

urlpatterns = [path("", include(router.urls))]
