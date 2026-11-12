from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.orders.views import KitchenViewSet

router = DefaultRouter()
router.register("", KitchenViewSet, basename="kitchen")
urlpatterns = [path("", include(router.urls))]
