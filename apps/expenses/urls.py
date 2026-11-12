from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.expenses.views import CategoryViewSet, ExpenseViewSet

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="exp-cats")
router.register("", ExpenseViewSet, basename="expenses")
urlpatterns = [path("", include(router.urls))]
