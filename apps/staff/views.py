from decimal import Decimal

from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.staff.models import Attendance, DailyWageSettlement, PayrollRun, Payslip, StaffProfile
from apps.staff.services import (
    create_manual_wage,
    generate_daily_wages,
    generate_monthly_payroll,
    haversine_m,
    parse_day,
    parse_period,
    settle_daily_wage,
    settle_payslip,
)


class StaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffProfile
        fields = (
            "id",
            "user",
            "name",
            "phone",
            "email",
            "role_title",
            "wage_type",
            "wage_amount",
            "joined_on",
            "is_active",
        )


class AttendanceSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)

    class Meta:
        model = Attendance
        fields = (
            "id",
            "staff",
            "staff_name",
            "date",
            "mark",
            "note",
            "latitude",
            "longitude",
            "check_in_at",
            "check_out_at",
            "distance_m",
        )


class WageSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    pending_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = DailyWageSettlement
        fields = (
            "id",
            "staff",
            "staff_name",
            "date",
            "amount",
            "approved",
            "source",
            "days",
            "payment_status",
            "amount_paid",
            "pending_amount",
        )


class PayslipSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    pending_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Payslip
        fields = (
            "id",
            "staff",
            "staff_name",
            "gross",
            "deductions",
            "net",
            "allowance",
            "overtime",
            "bonus",
            "advance",
            "wage_basis",
            "days_worked",
            "source",
            "payment_status",
            "amount_paid",
            "pending_amount",
        )


class PayrollSerializer(serializers.ModelSerializer):
    slips = PayslipSerializer(many=True, read_only=True)
    pending_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PayrollRun
        fields = (
            "id",
            "period",
            "status",
            "total",
            "amount_paid",
            "pending_amount",
            "source",
            "notes",
            "slips",
            "created_at",
        )


class StaffViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "staff"
    queryset = StaffProfile.objects.all()
    serializer_class = StaffSerializer
    search_fields = ["name", "phone"]


class AttendanceViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "staff"
    queryset = Attendance.objects.select_related("staff")
    serializer_class = AttendanceSerializer
    filterset_fields = ["date", "staff", "mark"]

    @action(detail=False, methods=["post"])
    def check_in(self, request):
        """Mark present for a staff member, optionally validating GPS against restaurant."""
        staff_id = request.data.get("staff")
        if not staff_id:
            return Response({"detail": "staff is required"}, status=400)
        staff = StaffProfile.objects.filter(restaurant=request.restaurant, pk=staff_id).first()
        if not staff:
            return Response({"detail": "Staff not found"}, status=404)

        from django.utils import timezone

        day = parse_day(request.data.get("date"))
        lat = request.data.get("latitude")
        lng = request.data.get("longitude")
        settings_obj = getattr(request.restaurant, "settings", None)
        distance = None
        if settings_obj and settings_obj.require_location_login:
            if lat is None or lng is None:
                return Response({"detail": "Location required for on-site check-in"}, status=400)
            if settings_obj.latitude is None or settings_obj.longitude is None:
                return Response({"detail": "Restaurant location is not configured"}, status=400)
            distance = Decimal(
                str(
                    round(
                        haversine_m(
                            float(lat),
                            float(lng),
                            float(settings_obj.latitude),
                            float(settings_obj.longitude),
                        ),
                        2,
                    )
                )
            )
            if distance > settings_obj.login_radius_m:
                return Response(
                    {
                        "detail": f"Outside restaurant geofence ({distance}m > {settings_obj.login_radius_m}m)",
                        "distance_m": distance,
                    },
                    status=403,
                )

        mark = request.data.get("mark") or Attendance.Mark.PRESENT
        att, _ = Attendance.objects.update_or_create(
            restaurant=request.restaurant,
            staff=staff,
            date=day,
            defaults={
                "mark": mark,
                "note": request.data.get("note") or "",
                "latitude": lat,
                "longitude": lng,
                "check_in_at": timezone.now(),
                "distance_m": distance,
            },
        )
        # Auto-create daily wage when daily staff checks in
        if staff.wage_type == "daily" and mark in (
            Attendance.Mark.PRESENT,
            Attendance.Mark.LATE,
            Attendance.Mark.HALF_DAY,
        ):
            generate_daily_wages(restaurant=request.restaurant, day=day, staff_ids=[staff.id])
        return Response(AttendanceSerializer(att).data, status=201)

    @action(detail=True, methods=["post"])
    def check_out(self, request, pk=None):
        """Record check-out time; optionally re-validate GPS."""
        from django.utils import timezone

        att = self.get_object()
        lat = request.data.get("latitude")
        lng = request.data.get("longitude")
        settings_obj = getattr(request.restaurant, "settings", None)
        if settings_obj and settings_obj.require_location_login:
            if lat is None or lng is None:
                return Response({"detail": "Location required for check-out"}, status=400)
            if settings_obj.latitude is None or settings_obj.longitude is None:
                return Response({"detail": "Restaurant location is not configured"}, status=400)
            distance = Decimal(
                str(
                    round(
                        haversine_m(
                            float(lat),
                            float(lng),
                            float(settings_obj.latitude),
                            float(settings_obj.longitude),
                        ),
                        2,
                    )
                )
            )
            if distance > settings_obj.login_radius_m:
                return Response(
                    {
                        "detail": f"Outside restaurant geofence ({distance}m > {settings_obj.login_radius_m}m)",
                        "distance_m": distance,
                    },
                    status=403,
                )
            att.distance_m = distance
            att.latitude = lat
            att.longitude = lng
        att.check_out_at = timezone.now()
        att.save(update_fields=["check_out_at", "distance_m", "latitude", "longitude", "updated_at"])
        return Response(AttendanceSerializer(att).data)


class WageViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "payroll"
    queryset = DailyWageSettlement.objects.select_related("staff")
    serializer_class = WageSerializer
    filterset_fields = ["date", "staff", "approved", "source"]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        obj = self.get_object()
        obj.approved = True
        obj.save(update_fields=["approved"])
        return Response(WageSerializer(obj).data)

    @action(detail=False, methods=["post"])
    def approve_all(self, request):
        qs = self.get_queryset().filter(restaurant=request.restaurant, approved=False)
        updated = qs.update(approved=True)
        return Response({"approved": updated})

    @action(detail=True, methods=["post"])
    def settle(self, request, pk=None):
        """Record full or partial payment against an approved daily wage."""
        obj = self.get_object()
        try:
            amount = Decimal(str(request.data.get("amount") if request.data.get("amount") is not None else obj.pending_amount))
            wage = settle_daily_wage(
                restaurant=request.restaurant,
                wage=obj,
                amount=amount,
                method=request.data.get("method") or "Cash",
                note=request.data.get("note") or "",
                user=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        log_action(request, "wage.settled", wage, {"amount": str(amount)})
        return Response(WageSerializer(wage).data)

    @action(detail=False, methods=["post"])
    def generate(self, request):
        """Auto-generate daily wages from today's (or given) attendance."""
        day = parse_day(request.data.get("date"))
        created = generate_daily_wages(restaurant=request.restaurant, day=day)
        return Response(
            {"created": len(created), "date": str(day), "items": WageSerializer(created, many=True).data}
        )

    def create(self, request, *args, **kwargs):
        staff_id = request.data.get("staff")
        amount = request.data.get("amount")
        if not staff_id or amount is None:
            return Response({"detail": "staff and amount are required"}, status=400)
        staff = StaffProfile.objects.filter(restaurant=request.restaurant, pk=staff_id).first()
        if not staff:
            return Response({"detail": "Staff not found"}, status=404)
        obj = create_manual_wage(
            restaurant=request.restaurant,
            staff=staff,
            day=parse_day(request.data.get("date")),
            amount=Decimal(str(amount)),
            days=Decimal(str(request.data.get("days") or 1)),
        )
        return Response(WageSerializer(obj).data, status=201)


class PayrollViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "payroll"
    queryset = PayrollRun.objects.prefetch_related("slips__staff")
    serializer_class = PayrollSerializer

    @action(detail=False, methods=["post"])
    def generate(self, request):
        """
        Auto or manual payroll run.
        body: { period?: "YYYY-MM", mode?: "auto"|"manual", slips?: [...] }
        """
        mode = request.data.get("mode") or "auto"
        period = parse_period(request.data.get("period"))
        try:
            run = generate_monthly_payroll(
                restaurant=request.restaurant,
                period=period,
                mode=mode,
                slips=request.data.get("slips") or [],
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        log_action(request, "payroll.generated", run, {"mode": mode, "period": period})
        return Response(PayrollSerializer(run).data, status=201)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        run = self.get_object()
        run.status = PayrollRun.Status.APPROVED
        run.save(update_fields=["status"])
        log_action(request, "payroll.processed", run)
        return Response(PayrollSerializer(run).data)

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        """Mark every unpaid/partial slip as fully paid (legacy bulk settle)."""
        run = self.get_object()
        if run.status == PayrollRun.Status.DRAFT:
            return Response({"detail": "Approve payroll before paying"}, status=400)
        for slip in run.slips.exclude(payment_status="paid"):
            pending = slip.pending_amount
            if pending > 0:
                settle_payslip(
                    restaurant=request.restaurant,
                    slip=slip,
                    amount=pending,
                    method=request.data.get("method") or "Cash",
                    note=request.data.get("note") or "Full settlement",
                    user=request.user,
                )
        run.refresh_from_db()
        return Response(PayrollSerializer(run).data)

    @action(detail=True, methods=["post"])
    def settle_slip(self, request, pk=None):
        """Full or partial payment against one payslip."""
        run = self.get_object()
        slip = run.slips.filter(pk=request.data.get("slip_id")).first()
        if not slip:
            return Response({"detail": "Payslip not found"}, status=404)
        try:
            amount = Decimal(
                str(
                    request.data.get("amount")
                    if request.data.get("amount") is not None
                    else slip.pending_amount
                )
            )
            settle_payslip(
                restaurant=request.restaurant,
                slip=slip,
                amount=amount,
                method=request.data.get("method") or "Cash",
                note=request.data.get("note") or "",
                user=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        run.refresh_from_db()
        log_action(request, "payroll.slip_settled", run, {"slip_id": str(slip.id), "amount": str(amount)})
        return Response(PayrollSerializer(run).data)

    @action(detail=True, methods=["post"])
    def update_slip(self, request, pk=None):
        """Manually adjust a payslip inside a draft/approved run."""
        run = self.get_object()
        if run.status in (PayrollRun.Status.PAID, PayrollRun.Status.PARTIAL) and run.amount_paid > 0:
            return Response({"detail": "Cannot edit a payroll that already has payments"}, status=400)
        if run.status == PayrollRun.Status.PAID:
            return Response({"detail": "Cannot edit a paid payroll"}, status=400)
        slip_id = request.data.get("slip_id")
        slip = run.slips.filter(pk=slip_id).first()
        if not slip:
            return Response({"detail": "Payslip not found"}, status=404)

        gross = Decimal(str(request.data.get("gross", request.data.get("basic", slip.gross))))
        allowance = Decimal(str(request.data.get("allowance", slip.allowance)))
        overtime = Decimal(str(request.data.get("overtime", slip.overtime)))
        bonus = Decimal(str(request.data.get("bonus", slip.bonus)))
        advance = Decimal(str(request.data.get("advance", slip.advance)))
        deduction = Decimal(
            str(request.data.get("deduction", request.data.get("deductions", max(slip.deductions - slip.advance, 0))))
        )
        slip.gross = gross
        slip.allowance = allowance
        slip.overtime = overtime
        slip.bonus = bonus
        slip.advance = advance
        slip.deductions = deduction + advance
        slip.net = gross + allowance + overtime + bonus - deduction - advance
        slip.source = "manual"
        slip.save()
        total = sum((s.net for s in run.slips.all()), Decimal("0"))
        run.total = total
        run.source = "manual"
        run.save(update_fields=["total", "source"])
        return Response(PayrollSerializer(run).data)
