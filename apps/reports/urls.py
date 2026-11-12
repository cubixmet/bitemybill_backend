from django.urls import path

from apps.reports.views import OverviewReport

urlpatterns = [path("overview/", OverviewReport.as_view())]
