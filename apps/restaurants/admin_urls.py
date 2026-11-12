from django.urls import path

from apps.restaurants.admin_views import (
    AdminCatalog,
    AdminLeads,
    AdminRestaurantList,
    AdminRunDebits,
    AdminUsageList,
    AdminUsers,
    AdminWalletList,
)

urlpatterns = [
    path("restaurants/", AdminRestaurantList.as_view()),
    path("wallets/", AdminWalletList.as_view()),
    path("usage/", AdminUsageList.as_view()),
    path("users/", AdminUsers.as_view()),
    path("leads/", AdminLeads.as_view()),
    path("catalog/", AdminCatalog.as_view()),
    path("billing/run/", AdminRunDebits.as_view()),
]
