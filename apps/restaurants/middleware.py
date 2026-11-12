from django.utils.deprecation import MiddlewareMixin
from rest_framework_simplejwt.authentication import JWTAuthentication


class RestaurantContextMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.restaurant = None
        request.membership = None
        if not request.path.startswith("/api/"):
            return
        restaurant_id = request.headers.get("X-Restaurant-Id") or request.META.get("HTTP_X_RESTAURANT_ID")
        if not restaurant_id:
            return
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            jwt = JWTAuthentication()
            try:
                auth = jwt.authenticate(request)
                if auth:
                    user = auth[0]
                    request.user = user
            except Exception:
                return
        if not getattr(user, "is_authenticated", False):
            return
        from apps.restaurants.models import Restaurant, RestaurantMembership

        try:
            restaurant = Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return
        if getattr(user, "user_kind", None) == "platform":
            request.restaurant = restaurant
            return
        membership = (
            RestaurantMembership.objects.select_related("role", "restaurant")
            .filter(user=user, restaurant=restaurant, is_active=True)
            .first()
        )
        if membership:
            request.restaurant = restaurant
            request.membership = membership
