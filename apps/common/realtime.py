from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def publish(group: str, event_type: str, payload: dict):
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        group,
        {"type": "ops.event", "event": event_type, "payload": payload},
    )


def restaurant_groups(restaurant_id, extra=None):
    groups = [
        f"restaurant_{restaurant_id}",
        f"kitchen_{restaurant_id}",
        f"orders_{restaurant_id}",
    ]
    if extra:
        groups.extend(extra)
    return groups
