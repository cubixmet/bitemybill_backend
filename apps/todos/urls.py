from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.todos.views import TodoViewSet

router = DefaultRouter()
router.register("", TodoViewSet, basename="todos")
urlpatterns = [path("", include(router.urls))]
