from django.urls import path

from apps.billing.views import LeadView

urlpatterns = [
    path("setup-requests/", LeadView.as_view()),
    path("contact/", LeadView.as_view()),
]
