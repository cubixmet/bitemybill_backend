from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.orders.views import TableViewSet

router = DefaultRouter()
router.register("", TableViewSet, basename="tables")
urlpatterns = [path("", include(router.urls))]
