from django.urls import path

from apps.billing.views import RunUsageView, WalletTopUpView, WalletVerifyView, WalletView

urlpatterns = [
    path("wallet/", WalletView.as_view()),
    path("wallet/top-up/", WalletTopUpView.as_view()),
    path("wallet/verify/", WalletVerifyView.as_view()),
    path("usage/run/", RunUsageView.as_view()),
]
