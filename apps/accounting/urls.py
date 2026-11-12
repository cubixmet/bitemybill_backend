from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounting.views import (
    AccountViewSet,
    BalanceSheetView,
    GstReportView,
    JournalViewSet,
    ProfitAndLossView,
    SyncBooksView,
    TrialBalanceView,
)

router = DefaultRouter()
router.register("accounts", AccountViewSet, basename="acct-accounts")
router.register("journals", JournalViewSet, basename="acct-journals")

urlpatterns = [
    path("trial-balance/", TrialBalanceView.as_view()),
    path("profit-and-loss/", ProfitAndLossView.as_view()),
    path("balance-sheet/", BalanceSheetView.as_view()),
    path("gst/", GstReportView.as_view()),
    path("sync/", SyncBooksView.as_view()),
    path("", include(router.urls)),
]
