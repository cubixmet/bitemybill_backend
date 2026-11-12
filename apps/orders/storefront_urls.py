from django.urls import path

from apps.orders.storefront import (
    StorefrontCheckoutView,
    StorefrontHomeView,
    StorefrontLoyaltyMeView,
    StorefrontLoyaltyRedeemView,
    StorefrontLoyaltyView,
    StorefrontMenuView,
    StorefrontOrdersView,
    StorefrontTablesView,
)

urlpatterns = [
    path("<slug:slug>/", StorefrontHomeView.as_view()),
    path("<slug:slug>/menu/", StorefrontMenuView.as_view()),
    path("<slug:slug>/tables/", StorefrontTablesView.as_view()),
    path("<slug:slug>/checkout/", StorefrontCheckoutView.as_view()),
    path("<slug:slug>/orders/", StorefrontOrdersView.as_view()),
    path("<slug:slug>/orders/<uuid:pk>/", StorefrontOrdersView.as_view()),
    path("<slug:slug>/loyalty/", StorefrontLoyaltyView.as_view()),
    path("<slug:slug>/loyalty/me/", StorefrontLoyaltyMeView.as_view()),
    path("<slug:slug>/loyalty/redeem/", StorefrontLoyaltyRedeemView.as_view()),
]
