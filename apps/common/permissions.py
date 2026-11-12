from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsPlatformAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and getattr(user, "user_kind", None) == "platform")


class HasRestaurantAccess(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, "user_kind", None) == "platform":
            return True
        return getattr(request, "restaurant", None) is not None and getattr(request, "membership", None) is not None


class HasRestaurantPermission(BasePermission):
    """Check membership role against view.permission_scope / permission_action."""

    def has_permission(self, request, view):
        if not HasRestaurantAccess().has_permission(request, view):
            return False
        if getattr(request.user, "user_kind", None) == "platform":
            return True
        scope = getattr(view, "permission_scope", None)
        if not scope:
            return True
        action = getattr(view, "permission_action", None)
        if action is None:
            action = "View" if request.method in SAFE_METHODS else _action_for_method(request.method)
        return request.membership.has_permission(scope, action)

    def has_object_permission(self, request, view, obj):
        restaurant = getattr(request, "restaurant", None)
        obj_restaurant = getattr(obj, "restaurant_id", None) or getattr(getattr(obj, "restaurant", None), "id", None)
        if restaurant and obj_restaurant and str(obj_restaurant) != str(restaurant.id):
            return False
        return True


def _action_for_method(method: str) -> str:
    return {
        "POST": "Create",
        "PUT": "Edit",
        "PATCH": "Edit",
        "DELETE": "Delete",
    }.get(method, "View")
