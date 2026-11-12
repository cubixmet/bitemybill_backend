from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.customers.views import CustomerViewSet

router = DefaultRouter()
router.register("", CustomerViewSet, basename="customers")
urlpatterns = [path("", include(router.urls))]
