from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.inventory.models import InventoryItem, InventoryLedger, PurchaseOrder, PurchasePayment, Unit


def convert(qty: Decimal, from_code: str, to_unit: Unit) -> Decimal:
    src = Unit.objects.filter(code=from_code).first()
    if not src:
        return qty
    base = Decimal(qty) * src.to_base
    return base / (to_unit.to_base or Decimal("1"))


@transaction.atomic
def post_ledger(
    *,
    restaurant,
    item: InventoryItem,
    entry_type: str,
    quantity: Decimal,
    user=None,
    reference="",
    note="",
    unit_cost=None,
    amount=None,
):
    """quantity is signed in the item's base unit."""
    quantity = Decimal(quantity)
    new_qty = item.quantity + quantity
    if new_qty < 0:
        raise ValueError(f"Insufficient stock for {item.name}")
    item.quantity = new_qty
    item.save(update_fields=["quantity", "updated_at"])
    uc = Decimal(unit_cost) if unit_cost is not None else None
    amt = Decimal(amount) if amount is not None else None
    if amt is None and uc is not None:
        amt = (abs(quantity) * uc).quantize(Decimal("0.01"))
    return InventoryLedger.objects.create(
        restaurant=restaurant,
        item=item,
        entry_type=entry_type,
        quantity=quantity,
        balance_after=item.quantity,
        unit_cost=uc,
        amount=amt,
        reference=reference,
        note=note,
        created_by=user,
    )


@transaction.atomic
def receive_purchase(po: PurchaseOrder, *, user=None):
    """
    Receive all outstanding line qty into inventory.
    Records unit_cost + line amount on the ledger so purchase price history is visible
    (e.g. 5 kg sugar @ ₹100 = ₹500).
    Updates InventoryItem.cost to this purchase's unit_cost (latest price).
    """
    if po.status == PurchaseOrder.Status.CANCELLED:
        raise ValueError("Cannot receive a cancelled purchase")
    if po.status == PurchaseOrder.Status.RECEIVED and all(
        Decimal(l.received_qty or 0) >= Decimal(l.quantity) for l in po.lines.all()
    ):
        raise ValueError("Purchase already fully received")

    any_received = False
    for line in po.lines.select_related("item", "item__unit"):
        remaining = Decimal(line.quantity) - Decimal(line.received_qty or 0)
        if remaining <= 0:
            continue
        unit_cost = Decimal(line.unit_cost)
        line_amount = (remaining * unit_cost).quantize(Decimal("0.01"))
        post_ledger(
            restaurant=po.restaurant,
            item=line.item,
            entry_type=InventoryLedger.Type.PURCHASE,
            quantity=remaining,
            user=user,
            reference=po.number,
            note=f"PO {po.number} · {remaining} @ ₹{unit_cost} = ₹{line_amount}",
            unit_cost=unit_cost,
            amount=line_amount,
        )
        line.item.cost = unit_cost
        if po.supplier_id and not line.item.supplier_name:
            line.item.supplier_name = po.supplier.name
        line.item.save(update_fields=["cost", "supplier_name", "updated_at"])
        line.received_qty = Decimal(line.received_qty or 0) + remaining
        line.save(update_fields=["received_qty", "updated_at"])
        any_received = True

    if not any_received:
        raise ValueError("Nothing left to receive on this purchase")

    po.status = PurchaseOrder.Status.RECEIVED
    po.save(update_fields=["status", "updated_at"])
    # Cash purchase: settle full amount on receive when not bought on credit
    if not po.is_credit and po.payment_status != PurchaseOrder.PaymentStatus.PAID and po.pending_amount > 0:
        settle_purchase(
            po,
            amount=po.pending_amount,
            method="cash",
            paid_at=timezone.localdate(),
            note="Paid on delivery",
            user=user,
        )
    return po


@transaction.atomic
def settle_purchase(
    po: PurchaseOrder,
    *,
    amount,
    method="cash",
    paid_at=None,
    note="",
    reference="",
    user=None,
):
    """
    Record a full or partial payment against a purchase (credit settlement).
    Pending to pay = total − amount_paid.
    """
    if po.status == PurchaseOrder.Status.CANCELLED:
        raise ValueError("Cannot settle a cancelled purchase")
    pay = Decimal(amount)
    if pay <= 0:
        raise ValueError("Payment amount must be greater than zero")
    pending = po.pending_amount
    if pending <= 0:
        raise ValueError("Purchase is already fully paid")
    if pay > pending:
        pay = pending

    payment = PurchasePayment.objects.create(
        restaurant=po.restaurant,
        purchase=po,
        amount=pay,
        method=method or "cash",
        paid_at=paid_at or timezone.localdate(),
        note=note or "",
        reference=reference or "",
        created_by=user,
    )
    po.amount_paid = Decimal(po.amount_paid or 0) + pay
    po.refresh_payment_status(save=True)
    return payment
