from datetime import date

from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting.defaults import ensure_chart_of_accounts
from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.accounting.services import (
    balance_sheet,
    create_journal,
    gst_summary,
    ledger_for_account,
    parse_day,
    post_journal,
    profit_and_loss,
    sync_operational_books,
    trial_balance,
)
from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = (
            "id",
            "code",
            "name",
            "account_type",
            "parent",
            "is_system",
            "is_active",
            "gst_applicable",
        )


class JournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)

    class Meta:
        model = JournalLine
        fields = ("id", "account", "account_code", "account_name", "debit", "credit", "narration")


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True, read_only=True)
    total_debit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    total_credit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = JournalEntry
        fields = (
            "id",
            "number",
            "date",
            "memo",
            "status",
            "reference",
            "posted_at",
            "lines",
            "total_debit",
            "total_credit",
            "created_at",
        )


class AccountViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "accounting"
    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    filterset_fields = ["account_type", "is_active", "gst_applicable"]
    search_fields = ["code", "name"]

    @action(detail=False, methods=["post"])
    def seed(self, request):
        created = ensure_chart_of_accounts(request.restaurant)
        return Response(
            {
                "created": len(created),
                "accounts": AccountSerializer(
                    Account.objects.filter(restaurant=request.restaurant), many=True
                ).data,
            }
        )

    @action(detail=True, methods=["get"])
    def ledger(self, request, pk=None):
        account = self.get_object()
        date_from = request.query_params.get("from")
        date_to = request.query_params.get("to")
        data = ledger_for_account(
            request.restaurant,
            account,
            date_from=parse_day(date_from) if date_from else None,
            date_to=parse_day(date_to) if date_to else None,
        )
        return Response(data)


class JournalViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "accounting"
    queryset = JournalEntry.objects.prefetch_related("lines__account")
    serializer_class = JournalEntrySerializer
    filterset_fields = ["status", "date"]
    search_fields = ["number", "memo", "reference"]

    def create(self, request, *args, **kwargs):
        lines = request.data.get("lines") or []
        try:
            entry = create_journal(
                restaurant=request.restaurant,
                entry_date=parse_day(request.data.get("date")),
                lines=lines,
                memo=request.data.get("memo") or "",
                reference=request.data.get("reference") or "",
                number=request.data.get("number"),
                post=bool(request.data.get("post", True)),
                user=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        log_action(request, "accounting.journal_created", entry)
        return Response(JournalEntrySerializer(entry).data, status=201)

    @action(detail=True, methods=["post"])
    def post_entry(self, request, pk=None):
        entry = self.get_object()
        try:
            post_journal(entry, user=request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(JournalEntrySerializer(entry).data)


class TrialBalanceView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "accounting"

    def get(self, request):
        as_of = request.query_params.get("as_of")
        return Response(trial_balance(request.restaurant, as_of=parse_day(as_of) if as_of else None))


class ProfitAndLossView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "accounting"

    def get(self, request):
        today = timezone.localdate()
        date_from = parse_day(request.query_params.get("from") or f"{today.year}-01-01")
        date_to = parse_day(request.query_params.get("to") or str(today))
        return Response(profit_and_loss(request.restaurant, date_from=date_from, date_to=date_to))


class BalanceSheetView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "accounting"

    def get(self, request):
        as_of = request.query_params.get("as_of")
        return Response(balance_sheet(request.restaurant, as_of=parse_day(as_of) if as_of else None))


class GstReportView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "accounting"

    def get(self, request):
        today = timezone.localdate()
        date_from = parse_day(request.query_params.get("from") or date(today.year, today.month, 1).isoformat())
        date_to = parse_day(request.query_params.get("to") or str(today))
        return Response(gst_summary(request.restaurant, date_from=date_from, date_to=date_to))


class SyncBooksView(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "accounting"
    permission_action = "Create"

    def post(self, request):
        day = parse_day(request.data.get("date"))
        result = sync_operational_books(request.restaurant, day=day)
        log_action(request, "accounting.books_synced", request.restaurant, result)
        return Response(result)
