from django.conf import settings
from django.db import models

from apps.common.models import RestaurantOwnedModel, TimeStampedModel


class DiningTable(RestaurantOwnedModel):
    class Status(models.TextChoices):
        FREE = "free", "Free"
        OCCUPIED = "occupied", "Occupied"
        BILLED = "billed", "Billed"
        RESERVED = "reserved", "Reserved"
        CLEANING = "cleaning", "Cleaning"

    code = models.CharField(max_length=20)
    zone = models.CharField(max_length=40, default="Main")
    seats = models.PositiveSmallIntegerField(default=4)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FREE)
    join_code = models.CharField(max_length=20, blank=True)

    class Meta:
        unique_together = ("restaurant", "code")
        ordering = ["code"]


class TableSession(RestaurantOwnedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        BILL_REQUESTED = "bill_requested", "Bill requested"
        CLOSED = "closed", "Closed"

    table = models.ForeignKey(DiningTable, on_delete=models.CASCADE, related_name="sessions")
    customer = models.ForeignKey(
        "customers.RestaurantCustomer", null=True, blank=True, on_delete=models.SET_NULL
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="opened_sessions"
    )
    guest_count = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)


class Order(RestaurantOwnedModel):
    class Type(models.TextChoices):
        DINE_IN = "dine_in", "Dine-in"
        TAKEAWAY = "takeaway", "Takeaway"
        DELIVERY = "delivery", "Delivery"

    class Channel(models.TextChoices):
        POS = "pos", "POS"
        PWA = "pwa", "PWA"
        QR = "qr", "QR"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        PREPARING = "preparing", "Preparing"
        READY = "ready", "Ready"
        SERVED = "served", "Served"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        PICKED_UP = "picked_up", "Picked up"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PARTIAL = "partial", "Partial"
        PAID = "paid", "Paid"
        REFUNDED = "refunded", "Refunded"

    number = models.CharField(max_length=24, db_index=True)
    order_type = models.CharField(max_length=20, choices=Type.choices)
    channel = models.CharField(max_length=12, choices=Channel.choices, default=Channel.POS)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    payment_status = models.CharField(max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    session = models.ForeignKey(TableSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    table = models.ForeignKey(DiningTable, null=True, blank=True, on_delete=models.SET_NULL)
    customer = models.ForeignKey(
        "customers.RestaurantCustomer", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )
    customer_name = models.CharField(max_length=120, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    packaging_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_mode = models.CharField(max_length=40, blank=True)
    placed_at = models.DateTimeField(auto_now_add=True)
    pickup_at = models.DateTimeField(null=True, blank=True)
    delivery_address = models.TextField(blank=True)

    class Meta:
        ordering = ["-placed_at"]
        indexes = [
            models.Index(fields=["restaurant", "status"]),
            models.Index(fields=["restaurant", "placed_at"]),
        ]


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    menu_item = models.ForeignKey("menu.MenuItem", null=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=160)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    station = models.CharField(max_length=40, blank=True)
    kitchen_status = models.CharField(max_length=20, default="queued")


class OrderItemModifier(TimeStampedModel):
    item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="modifiers")
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)


class KitchenTicket(RestaurantOwnedModel):
    class Stage(models.TextChoices):
        NEW = "new", "New"
        PREPARING = "preparing", "Preparing"
        READY = "ready", "Ready"
        COMPLETED = "completed", "Completed"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="tickets")
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.NEW)
    priority = models.CharField(max_length=12, default="normal")
    sla_minutes = models.PositiveIntegerField(default=18)
    note = models.CharField(max_length=255, blank=True)


class KitchenTicketItem(TimeStampedModel):
    ticket = models.ForeignKey(KitchenTicket, on_delete=models.CASCADE, related_name="lines")
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    station = models.CharField(max_length=40, blank=True)
    instructions = models.CharField(max_length=255, blank=True)
    stage = models.CharField(max_length=20, default="queued")
