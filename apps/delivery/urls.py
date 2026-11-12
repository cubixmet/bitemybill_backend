from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.delivery.views import AgentViewSet, JobViewSet

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agents")
router.register("jobs", JobViewSet, basename="jobs")
urlpatterns = [path("", include(router.urls))]
