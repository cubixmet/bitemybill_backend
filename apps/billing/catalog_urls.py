from django.urls import path

from apps.billing.views import CatalogView

urlpatterns = [path("modules/", CatalogView.as_view())]
