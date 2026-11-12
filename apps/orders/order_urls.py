from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.orders.views import OrderViewSet

router = DefaultRouter()
router.register("", OrderViewSet, basename="orders")
urlpatterns = [path("", include(router.urls))]
