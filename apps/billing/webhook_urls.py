from django.urls import path

from apps.billing.views import RazorpayWebhookView

urlpatterns = [path("razorpay/", RazorpayWebhookView.as_view())]
