from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.staff.views import AttendanceViewSet, PayrollViewSet, StaffViewSet, WageViewSet

router = DefaultRouter()
router.register("employees", StaffViewSet, basename="staff")
router.register("attendance", AttendanceViewSet, basename="attendance")
router.register("wages", WageViewSet, basename="wages")
router.register("payroll", PayrollViewSet, basename="payroll")
urlpatterns = [path("", include(router.urls))]
