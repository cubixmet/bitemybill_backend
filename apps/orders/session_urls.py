from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.orders.views import SessionViewSet

router = DefaultRouter()
router.register("", SessionViewSet, basename="sessions")
urlpatterns = [path("", include(router.urls))]
