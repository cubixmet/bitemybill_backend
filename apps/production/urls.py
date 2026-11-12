from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.production.views import BatchViewSet, RecipeViewSet

router = DefaultRouter()
router.register("recipes", RecipeViewSet, basename="recipes")
router.register("batches", BatchViewSet, basename="batches")
urlpatterns = [path("", include(router.urls))]
