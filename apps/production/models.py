from django.db import models

from apps.common.models import RestaurantOwnedModel, TimeStampedModel


class Recipe(RestaurantOwnedModel):
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=80, blank=True)
    yield_qty = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    yield_unit = models.CharField(max_length=40, default="pcs")
    batch_time_mins = models.PositiveIntegerField(default=30)
    station = models.CharField(max_length=40, blank=True)
    steps = models.JSONField(default=list)
    prep_type = models.CharField(max_length=32, default="Pre-prepared")
    # Finished product stocked on complete; leftover/balance wastage posts against this item.
    output_item = models.ForeignKey(
        "inventory.InventoryItem",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="produced_by_recipes",
    )
    # Overhead for one full yield batch (permission-gated via recipe_costs scope).
    electricity_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gas_fuel_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    labor_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # time / effort
    # Optional sell price for the full batch yield — used for profit margin.
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def overhead_cost(self):
        from decimal import Decimal

        return (
            Decimal(self.electricity_cost or 0)
            + Decimal(self.gas_fuel_cost or 0)
            + Decimal(self.labor_cost or 0)
        )

    def ingredient_cost(self):
        from decimal import Decimal

        from apps.inventory.services import convert

        cached = getattr(self, "_ingredient_cost_cache", None)
        if cached is not None:
            return cached

        total = Decimal("0")
        # Use .all() so prefetch_related("ingredients__inventory_item") is honored
        # (chained .select_related() would force a fresh query per recipe).
        for ing in self.ingredients.all():
            item = ing.inventory_item
            qty = convert(Decimal(ing.quantity), ing.unit_code, item.unit)
            total += (qty * Decimal(item.cost or 0)).quantize(Decimal("0.01"))
        self._ingredient_cost_cache = total
        return total

    def total_cost(self):
        cached = getattr(self, "_total_cost_cache", None)
        if cached is not None:
            return cached
        total = self.ingredient_cost() + self.overhead_cost()
        self._total_cost_cache = total
        return total

    def profit_amount(self):
        from decimal import Decimal

        sell = Decimal(self.selling_price or 0)
        if sell <= 0:
            return None
        return sell - self.total_cost()


class RecipeIngredient(TimeStampedModel):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ingredients")
    inventory_item = models.ForeignKey("inventory.InventoryItem", on_delete=models.PROTECT)
    # Gross amount taken from stock for one recipe yield (e.g. 5 kg whole watermelon).
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    unit_code = models.CharField(max_length=16)
    # Unusable portion of that gross input (e.g. 0.5 kg shell). Rest is usable yield.
    wastage_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    # Optional alternate: wastage as % of quantity when wastage_qty is 0.
    wastage_pct = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    wastage_note = models.CharField(max_length=80, blank=True)  # e.g. "shell", "trimming"
    note = models.CharField(max_length=80, blank=True)

    def resolved_wastage(self):
        from decimal import Decimal

        if self.wastage_qty and self.wastage_qty > 0:
            return Decimal(self.wastage_qty)
        if self.wastage_pct and self.wastage_pct > 0:
            return (Decimal(self.quantity) * Decimal(self.wastage_pct) / Decimal("100")).quantize(Decimal("0.0001"))
        return Decimal("0")


class ProductionBatch(RestaurantOwnedModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name="batches")
    product_name = models.CharField(max_length=160)
    unit = models.CharField(max_length=40, default="pcs")
    planned_qty = models.DecimalField(max_digits=14, decimal_places=4)
    actual_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    # Finished-product leftover / unsold balance written off as wastage.
    wastage_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    shift = models.CharField(max_length=20, blank=True)
    staff_name = models.CharField(max_length=120, blank=True)
    note = models.CharField(max_length=255, blank=True)
    posted = models.BooleanField(default=False)
