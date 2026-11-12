from decimal import Decimal

from django.db import transaction

from apps.loyalty.models import LoyaltyLedger, LoyaltyProgram, RewardRedemption


@transaction.atomic
def earn_points(customer, points: Decimal, *, restaurant, reference="", note=""):
    points = Decimal(points)
    customer.points_balance += points
    customer.save(update_fields=["points_balance", "updated_at"])
    LoyaltyLedger.objects.create(
        restaurant=restaurant,
        customer=customer,
        kind=LoyaltyLedger.Kind.EARNED,
        points=points,
        balance_after=customer.points_balance,
        reference=reference,
        note=note,
    )
    return customer


@transaction.atomic
def redeem_reward(*, restaurant, customer, reward, order=None):
    if not reward.is_active:
        raise ValueError("Reward inactive")
    if customer.points_balance < reward.points_cost:
        raise ValueError("Insufficient points")
    if order and reward.min_order and order.total < reward.min_order:
        raise ValueError("Order below minimum")
    customer.points_balance -= reward.points_cost
    customer.save(update_fields=["points_balance", "updated_at"])
    LoyaltyLedger.objects.create(
        restaurant=restaurant,
        customer=customer,
        kind=LoyaltyLedger.Kind.REDEEMED,
        points=-reward.points_cost,
        balance_after=customer.points_balance,
        reference=str(reward.id),
        note=reward.title,
    )
    return RewardRedemption.objects.create(
        restaurant=restaurant,
        customer=customer,
        reward=reward,
        order=order,
        points_spent=reward.points_cost,
    )


def points_for_order(restaurant, order_total: Decimal) -> Decimal:
    program = LoyaltyProgram.objects.filter(restaurant=restaurant, is_active=True).first()
    if not program or not program.amount_step:
        return Decimal("0")
    steps = (order_total / program.amount_step).to_integral_value()
    return steps * program.points_per_amount
