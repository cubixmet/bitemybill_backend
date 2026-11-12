from decimal import Decimal

from django.db import models

from apps.common.models import RestaurantOwnedModel, TimeStampedModel


class Unit(TimeStampedModel):
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=40)
    kind = models.CharField(max_length=20, default="count")  # mass, volume, count
    to_base = models.DecimalField(max_digits=14, decimal_places=6, default=1)


class InventoryItem(RestaurantOwnedModel):
    name = models.CharField(max_length=160)
    sku = models.CharField(max_length=40, blank=True)
    category = models.CharField(max_length=80, blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    minimum = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    # Latest purchase unit cost (updated on receive); history lives on PurchaseItem / ledger.
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    supplier_name = models.CharField(max_length=160, blank=True)
    expiry = models.DateField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("restaurant", "name")


class InventoryLedger(RestaurantOwnedModel):
    class Type(models.TextChoices):
        OPENING = "opening", "Opening Stock"
        PURCHASE = "purchase", "Purchase"
        PRODUCTION_CONSUMPTION = "production_consumption", "Production Consumption"
        SALES_CONSUMPTION = "sales_consumption", "Sales Consumption"
        RETURN = "return", "Return"
        ADJUSTMENT = "adjustment", "Adjustment"
        WASTE = "waste", "Waste"
        STOCK_IN = "stock_in", "Stock In"
        STOCK_OUT = "stock_out", "Stock Out"

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="ledger")
    entry_type = models.CharField(max_length=32, choices=Type.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=4)  # signed
    balance_after = models.DecimalField(max_digits=14, decimal_places=4)
    # Purchase unit price at the time of this movement (₹ per item unit).
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Line amount for purchases (qty × unit_cost), positive for stock-in purchases.
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["restaurant", "item", "created_at"])]


class Supplier(RestaurantOwnedModel):
    name = models.CharField(max_length=160)
    contact = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    payment_terms = models.CharField(max_length=40, blank=True, default="Net 15")

    def outstanding_balance(self) -> Decimal:
        # Annotated list views set outstanding_total to avoid N+1.
        annotated = getattr(self, "outstanding_total", None)
        if annotated is not None:
            return Decimal(annotated or 0)
        qs = self.purchase_orders.exclude(status=PurchaseOrder.Status.CANCELLED).exclude(
            payment_status=PurchaseOrder.PaymentStatus.PAID
        )
        total = Decimal("0")
        for po in qs.only("total", "amount_paid"):
            pending = Decimal(po.total or 0) - Decimal(po.amount_paid or 0)
            if pending > 0:
                total += pending
        return total


class PurchaseOrder(RestaurantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ORDERED = "ordered", "Ordered"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"
        # Legacy — treat as received + paid when encountered
        PAID = "paid", "Paid"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PARTIAL = "partial", "Partial"
        PAID = "paid", "Paid"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    number = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ORDERED)
    # Credit by default: goods received, money owed until settlements cover total.
    is_credit = models.BooleanField(default=True)
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    @property
    def pending_amount(self) -> Decimal:
        pending = Decimal(self.total or 0) - Decimal(self.amount_paid or 0)
        return pending if pending > 0 else Decimal("0")

    def refresh_payment_status(self, save=True):
        paid = Decimal(self.amount_paid or 0)
        total = Decimal(self.total or 0)
        if paid <= 0:
            self.payment_status = self.PaymentStatus.UNPAID
        elif total > 0 and paid >= total:
            self.payment_status = self.PaymentStatus.PAID
            self.amount_paid = total
        else:
            self.payment_status = self.PaymentStatus.PARTIAL
        if save:
            self.save(update_fields=["payment_status", "amount_paid", "updated_at"])

    def recompute_total(self, save=True):
        subtotal = Decimal("0")
        for line in self.lines.all():
            subtotal += Decimal(line.quantity) * Decimal(line.unit_cost)
        tax = (subtotal * Decimal(self.tax_rate or 0) / Decimal("100")).quantize(Decimal("0.01"))
        self.total = (subtotal + tax).quantize(Decimal("0.01"))
        if save:
            self.save(update_fields=["total", "updated_at"])
            self.refresh_payment_status(save=True)
        return self.total


class PurchaseItem(TimeStampedModel):
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    # Exact unit price for this purchase (e.g. ₹100/kg when 5 kg = ₹500).
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    received_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    @property
    def line_amount(self) -> Decimal:
        return (Decimal(self.quantity) * Decimal(self.unit_cost)).quantize(Decimal("0.01"))


class PurchasePayment(RestaurantOwnedModel):
    """Partial or full settlement against a credit (or unpaid) purchase."""

    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=40, blank=True, default="cash")
    paid_at = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=80, blank=True)


class StockReturn(RestaurantOwnedModel):
    supplier = models.ForeignKey(Supplier, null=True, on_delete=models.SET_NULL)
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, default="open")
