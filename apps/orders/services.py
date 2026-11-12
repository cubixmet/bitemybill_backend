from decimal import Decimal

from django.db import transaction

from apps.common.realtime import publish
from apps.orders.models import KitchenTicket, KitchenTicketItem, Order, OrderItem, OrderItemModifier


def _tax_rate(restaurant):
    settings_obj = getattr(restaurant, "settings", None)
    if settings_obj:
        return Decimal(settings_obj.tax_rate) / Decimal("100")
    return Decimal("0.05")


def compute_totals(order: Order):
    subtotal = Decimal("0")
    for item in order.items.all().prefetch_related("modifiers"):
        line = item.unit_price * item.quantity
        for mod in item.modifiers.all():
            line += mod.price * item.quantity
        subtotal += line
    settings_obj = getattr(order.restaurant, "settings", None)
    tax_inclusive = bool(settings_obj and settings_obj.tax_inclusive)
    taxable = max(subtotal - order.discount, Decimal("0"))
    rate = _tax_rate(order.restaurant)
    if tax_inclusive:
        tax = taxable - (taxable / (Decimal("1") + rate))
        total = taxable + order.delivery_fee + order.packaging_fee
    else:
        tax = (taxable * rate).quantize(Decimal("0.01"))
        total = taxable + tax + order.delivery_fee + order.packaging_fee
    order.subtotal = subtotal
    order.tax = tax
    order.total = total
    order.save(update_fields=["subtotal", "tax", "total", "updated_at"])
    return order


def next_order_number(restaurant):
    prefix = restaurant.order_prefix or "BB"
    count = Order.objects.filter(restaurant=restaurant).count() + 1
    return f"{prefix}-{1000 + count}"


@transaction.atomic
def create_order(*, restaurant, user, data: dict) -> Order:
    items_data = data.pop("items", [])
    order = Order.objects.create(
        restaurant=restaurant,
        created_by=user,
        number=data.get("number") or next_order_number(restaurant),
        order_type=data["order_type"],
        channel=data.get("channel", Order.Channel.POS),
        session_id=data.get("session"),
        table_id=data.get("table"),
        customer_id=data.get("customer"),
        customer_name=data.get("customer_name") or "",
        customer_phone=data.get("customer_phone") or "",
        notes=data.get("notes") or "",
        discount=Decimal(data.get("discount") or 0),
        delivery_fee=Decimal(data.get("delivery_fee") or 0),
        packaging_fee=Decimal(data.get("packaging_fee") or 0),
        payment_mode=data.get("payment_mode") or "",
        delivery_address=data.get("delivery_address") or "",
        status=data.get("status") or Order.Status.PENDING,
    )
    for row in items_data:
        oi = OrderItem.objects.create(
            order=order,
            menu_item_id=row.get("menu_item"),
            name=row["name"],
            quantity=Decimal(row.get("quantity") or 1),
            unit_price=Decimal(row["unit_price"]),
            notes=row.get("notes") or "",
            station=row.get("station") or "",
        )
        for mod in row.get("modifiers") or []:
            OrderItemModifier.objects.create(item=oi, name=mod["name"], price=Decimal(mod.get("price") or 0))
    compute_totals(order)
    # Delivery orders get a job so Growth → Delivery + delivery-done customers stay in sync
    if order.order_type == Order.Type.DELIVERY:
        from apps.delivery.models import DeliveryJob

        DeliveryJob.objects.get_or_create(
            order=order,
            defaults={
                "restaurant": restaurant,
                "address": order.delivery_address or "",
                "fee": order.delivery_fee,
            },
        )
        if order.customer_id:
            from apps.customers.models import RestaurantCustomer

            RestaurantCustomer.objects.filter(pk=order.customer_id, source=RestaurantCustomer.Source.WALK_IN).update(
                source=RestaurantCustomer.Source.DELIVERY
            )
    publish(f"orders_{restaurant.id}", "order.created", {"id": str(order.id), "number": order.number})
    return order


def maybe_earn_loyalty(order: Order):
    """Credit loyalty points once when an order reaches completed + paid."""
    if order.status != Order.Status.COMPLETED:
        return None
    if order.payment_status != Order.PaymentStatus.PAID:
        return None
    if not order.customer_id:
        return None
    from apps.loyalty.models import LoyaltyLedger
    from apps.loyalty.services import earn_points, points_for_order

    already = LoyaltyLedger.objects.filter(
        restaurant=order.restaurant,
        customer_id=order.customer_id,
        kind=LoyaltyLedger.Kind.EARNED,
        reference=str(order.id),
    ).exists()
    if already:
        return None
    pts = points_for_order(order.restaurant, order.total)
    if pts <= 0:
        return None
    return earn_points(
        order.customer,
        pts,
        restaurant=order.restaurant,
        reference=str(order.id),
        note=f"Order {order.number}",
    )


@transaction.atomic
def send_kot(order: Order, user=None):
    ticket = KitchenTicket.objects.create(
        restaurant=order.restaurant,
        created_by=user,
        order=order,
        note=order.notes,
    )
    for item in order.items.all():
        KitchenTicketItem.objects.create(
            ticket=ticket,
            order_item=item,
            name=item.name,
            quantity=item.quantity,
            station=item.station,
            instructions=item.notes,
        )
        item.kitchen_status = "queued"
        item.save(update_fields=["kitchen_status"])
    if order.status == Order.Status.PENDING:
        order.status = Order.Status.CONFIRMED
        order.save(update_fields=["status", "updated_at"])
    payload = {"id": str(ticket.id), "order_id": str(order.id), "number": order.number}
    publish(f"kitchen_{order.restaurant_id}", "kitchen.ticket", payload)
    publish(f"orders_{order.restaurant_id}", "order.updated", payload)
    if order.session_id:
        publish(f"table_session_{order.session_id}", "order.updated", payload)
    publish(f"order_{order.id}", "order.updated", {"status": order.status})
    return ticket
