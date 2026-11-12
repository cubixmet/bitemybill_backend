from django.urls import path

from apps.audit.views import AuditListView

urlpatterns = [path("", AuditListView.as_view())]
