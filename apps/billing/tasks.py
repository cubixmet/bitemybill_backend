from celery import shared_task

from apps.billing.services import apply_monthly_debit
from apps.restaurants.models import Restaurant


@shared_task
def debit_all_restaurants():
    for restaurant in Restaurant.objects.filter(is_archived=False):
        apply_monthly_debit(restaurant)
