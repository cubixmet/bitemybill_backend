from decimal import Decimal

from django.db import transaction

from apps.inventory.models import InventoryItem, InventoryLedger, Unit
from apps.inventory.services import convert, post_ledger
from apps.production.models import ProductionBatch, Recipe


def _ingredient_waste(ing) -> Decimal:
    return ing.resolved_wastage()


def ensure_output_item(recipe: Recipe) -> InventoryItem | None:
    """Return finished-goods inventory item for the recipe, creating one if needed."""
    if recipe.output_item_id:
        return recipe.output_item

    unit = Unit.objects.filter(code__iexact=(recipe.yield_unit or "pcs")[:16]).first()
    if not unit:
        unit = Unit.objects.filter(code="pcs").first() or Unit.objects.first()
    if not unit:
        return None

    item, _ = InventoryItem.objects.get_or_create(
        restaurant=recipe.restaurant,
        name=recipe.name,
        defaults={
            "category": recipe.category or "Prepared",
            "unit": unit,
            "quantity": Decimal("0"),
            "minimum": Decimal("0"),
            "cost": Decimal("0"),
        },
    )
    recipe.output_item = item
    recipe.save(update_fields=["output_item", "updated_at"])
    return item


def _post_finished_balance_waste(*, batch: ProductionBatch, qty: Decimal, user=None, note=""):
    """Write off leftover finished food against the recipe output inventory item."""
    if qty <= 0:
        return None
    output = ensure_output_item(batch.recipe)
    if not output:
        raise ValueError("No finished-goods inventory item available for balance wastage")
    waste_qty = convert(qty, batch.unit or output.unit.code, output.unit)
    if waste_qty > output.quantity:
        waste_qty = output.quantity
    if waste_qty <= 0:
        return None
    return post_ledger(
        restaurant=batch.restaurant,
        item=output,
        entry_type=InventoryLedger.Type.WASTE,
        quantity=-waste_qty,
        user=user,
        reference=str(batch.id),
        note=note or f"Balance / leftover wastage · {batch.product_name}",
    )


@transaction.atomic
def complete_batch(batch: ProductionBatch, *, actual=None, wastage=None, user=None):
    """
    Deduct recipe ingredients, stock-in finished goods, then write off leftover balance.

    Ingredient wastage (shell/trim) → WASTE on ingredient items.
    Finished leftover / unsold balance → WASTE on recipe output item.
    """
    if batch.posted:
        return batch
    if actual is not None:
        batch.actual_qty = Decimal(actual)
    leftover = Decimal(wastage) if wastage is not None else Decimal(batch.wastage_qty or 0)
    if leftover < 0:
        leftover = Decimal("0")

    produced = batch.actual_qty or batch.planned_qty
    if leftover > produced:
        leftover = produced
    batch.wastage_qty = leftover

    scale = Decimal("1")
    if batch.recipe.yield_qty:
        scale = produced / batch.recipe.yield_qty

    for ing in batch.recipe.ingredients.select_related("inventory_item", "inventory_item__unit"):
        gross = Decimal(ing.quantity) * scale
        waste = _ingredient_waste(ing) * scale
        if waste > gross:
            waste = gross
        usable = gross - waste

        if usable:
            qty = convert(usable, ing.unit_code, ing.inventory_item.unit)
            post_ledger(
                restaurant=batch.restaurant,
                item=ing.inventory_item,
                entry_type=InventoryLedger.Type.PRODUCTION_CONSUMPTION,
                quantity=-qty,
                user=user,
                reference=str(batch.id),
                note=f"Production {batch.product_name} · usable",
            )
        if waste:
            qty_w = convert(waste, ing.unit_code, ing.inventory_item.unit)
            label = ing.wastage_note or "recipe wastage"
            post_ledger(
                restaurant=batch.restaurant,
                item=ing.inventory_item,
                entry_type=InventoryLedger.Type.WASTE,
                quantity=-qty_w,
                user=user,
                reference=str(batch.id),
                note=f"Recipe wastage ({label}) · {batch.product_name}",
            )

    output = ensure_output_item(batch.recipe)
    if output and produced > 0:
        in_qty = convert(produced, batch.unit or output.unit.code, output.unit)
        post_ledger(
            restaurant=batch.restaurant,
            item=output,
            entry_type=InventoryLedger.Type.STOCK_IN,
            quantity=in_qty,
            user=user,
            reference=str(batch.id),
            note=f"Production output · {batch.product_name}",
        )
        if leftover > 0:
            _post_finished_balance_waste(
                batch=batch,
                qty=leftover,
                user=user,
                note=f"Balance / leftover wastage at complete · {batch.product_name}",
            )

    batch.status = ProductionBatch.Status.COMPLETED
    batch.posted = True
    batch.save()
    return batch


@transaction.atomic
def record_balance_waste(batch: ProductionBatch, *, quantity: Decimal, user=None, note=""):
    """
    After service: leftover finished food still on hand becomes wastage.
    Deducts from recipe output inventory and accumulates on batch.wastage_qty.
    """
    qty = Decimal(quantity)
    if qty <= 0:
        raise ValueError("Balance wastage quantity must be greater than zero")
    if batch.status != ProductionBatch.Status.COMPLETED:
        raise ValueError("Complete the batch before recording leftover balance wastage")

    entry = _post_finished_balance_waste(
        batch=batch,
        qty=qty,
        user=user,
        note=note or f"Balance / leftover wastage · {batch.product_name}",
    )
    if entry is None:
        raise ValueError("No finished stock available to write off")
    batch.wastage_qty = Decimal(batch.wastage_qty or 0) + qty
    if note:
        batch.note = (batch.note + " · " if batch.note else "") + note
    batch.save(update_fields=["wastage_qty", "note", "updated_at"])
    return batch
