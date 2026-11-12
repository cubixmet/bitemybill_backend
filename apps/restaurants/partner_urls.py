from django.urls import path

from apps.restaurants.partner_views import (
    PartnerLoyaltyView,
    PartnerMenuView,
    PartnerOrdersView,
    PartnerRestaurantView,
)

urlpatterns = [
    path("restaurant/", PartnerRestaurantView.as_view()),
    path("menu/", PartnerMenuView.as_view()),
    path("orders/", PartnerOrdersView.as_view()),
    path("loyalty/", PartnerLoyaltyView.as_view()),
]
