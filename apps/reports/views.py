from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasRestaurantPermission
from apps.expenses.models import Expense
from apps.inventory.models import InventoryItem
from apps.orders.models import DiningTable, KitchenTicket, Order, OrderItem
from apps.production.models import ProductionBatch
from apps.staff.models import Attendance, PayrollRun


class OverviewReport(APIView):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "reports"

    def get(self, request):
        r = request.restaurant
        today = timezone.localdate()
        start_week = today - timedelta(days=6)
        orders = Order.objects.filter(restaurant=r, placed_at__date=today).exclude(status="cancelled")
        week = Order.objects.filter(restaurant=r, placed_at__date__gte=start_week).exclude(status="cancelled")
        sales = orders.aggregate(s=Sum("total"), n=Count("id"))
        by_type = list(orders.values("order_type").annotate(count=Count("id"), amount=Sum("total")))
        expenses = Expense.objects.filter(restaurant=r, date=today).aggregate(s=Sum("amount"))
        kitchen_open = KitchenTicket.objects.filter(restaurant=r).exclude(stage="completed").count()
        table_stats = DiningTable.objects.filter(restaurant=r).aggregate(
            total=Count("id"),
            occupied=Count("id", filter=Q(status="occupied")),
        )
        low_stock = [
            {"id": str(i.id), "name": i.name, "quantity": i.quantity, "minimum": i.minimum, "unit": i.unit.code}
            for i in InventoryItem.objects.filter(
                restaurant=r, deleted_at__isnull=True, quantity__lte=F("minimum")
            ).select_related("unit")
        ]
        by_day = {
            row["placed_at__date"]: row
            for row in week.values("placed_at__date").annotate(s=Sum("total"), n=Count("id"))
        }
        daily_sales = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            agg = by_day.get(day) or {}
            daily_sales.append({"date": str(day), "sales": agg.get("s") or 0, "orders": agg.get("n") or 0})
        item_sales = list(
            OrderItem.objects.filter(order__restaurant=r, order__placed_at__date=today)
            .exclude(order__status="cancelled")
            .values("name")
            .annotate(qty=Sum("quantity"), amount=Sum("unit_price"))
            .order_by("-qty")[:8]
        )
        production = list(
            ProductionBatch.objects.filter(restaurant=r, created_at__date=today).values(
                "product_name", "planned_qty", "actual_qty", "wastage_qty", "status"
            )
        )
        payroll = PayrollRun.objects.filter(restaurant=r).order_by("-created_at").first()
        present = Attendance.objects.filter(restaurant=r, date=today, mark="present").count()
        recent = Order.objects.filter(restaurant=r).order_by("-placed_at")[:8]
        return Response(
            {
                "date": str(today),
                "sales": sales["s"] or 0,
                "orders": sales["n"] or 0,
                "expenses": expenses["s"] or 0,
                "by_type": by_type,
                "kitchen_open": kitchen_open,
                "tables_total": table_stats["total"] or 0,
                "tables_occupied": table_stats["occupied"] or 0,
                "low_stock": low_stock,
                "daily_sales": daily_sales,
                "item_sales": item_sales,
                "production": production,
                "payroll_total": payroll.total if payroll else 0,
                "attendance_present": present,
                "recent_orders": [
                    {
                        "id": str(o.id),
                        "number": o.number,
                        "customer_name": o.customer_name,
                        "order_type": o.order_type,
                        "status": o.status,
                        "payment_status": o.payment_status,
                        "total": o.total,
                        "placed_at": o.placed_at,
                    }
                    for o in recent
                ],
                "profit": (sales["s"] or Decimal("0")) - (expenses["s"] or Decimal("0")),
            }
        )
