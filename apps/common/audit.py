from apps.audit.models import AuditLog


def log_action(request, action: str, instance=None, payload=None):
    restaurant = getattr(request, "restaurant", None)
    AuditLog.objects.create(
        restaurant=restaurant,
        actor=request.user if getattr(request.user, "is_authenticated", False) else None,
        action=action,
        object_type=instance.__class__.__name__ if instance is not None else "",
        object_id=str(getattr(instance, "pk", "") or ""),
        payload=payload or {},
        ip=request.META.get("REMOTE_ADDR"),
    )
