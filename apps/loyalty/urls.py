from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.loyalty.views import LedgerViewSet, ProgramViewSet, RedeemView, RewardViewSet, RuleViewSet

router = DefaultRouter()
router.register("programs", ProgramViewSet, basename="loyalty-programs")
router.register("rules", RuleViewSet, basename="loyalty-rules")
router.register("rewards", RewardViewSet, basename="loyalty-rewards")
router.register("ledger", LedgerViewSet, basename="loyalty-ledger")

urlpatterns = [
    path("redeem/", RedeemView.as_view()),
    path("", include(router.urls)),
]
