from django.db import models

from apps.common.models import RestaurantOwnedModel, SoftDeleteModel


class MenuCategory(RestaurantOwnedModel, SoftDeleteModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("restaurant", "slug")
        ordering = ["sort_order", "name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            self.slug = slugify(self.name)[:80] or "item"
        super().save(*args, **kwargs)


class MenuItem(RestaurantOwnedModel, SoftDeleteModel):
    category = models.ForeignKey(MenuCategory, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_available = models.BooleanField(default=True)
    is_veg = models.BooleanField(default=False)
    prep_minutes = models.PositiveIntegerField(default=15)
    station = models.CharField(max_length=40, blank=True)
    image_url = models.URLField(blank=True)
    tags = models.JSONField(default=list)
    points = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["name"]


class MenuModifierGroup(RestaurantOwnedModel):
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="modifier_groups")
    name = models.CharField(max_length=80)
    required = models.BooleanField(default=False)
    min_select = models.PositiveSmallIntegerField(default=0)
    max_select = models.PositiveSmallIntegerField(default=1)


class MenuModifierOption(RestaurantOwnedModel):
    group = models.ForeignKey(MenuModifierGroup, on_delete=models.CASCADE, related_name="options")
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
