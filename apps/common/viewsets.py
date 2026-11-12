from rest_framework.viewsets import ModelViewSet


class TenantModelViewSet(ModelViewSet):
    permission_scope = None
    filterset_fields = []
    search_fields = []
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        restaurant = getattr(self.request, "restaurant", None)
        if restaurant is None:
            return qs.none()
        field_names = {f.name for f in qs.model._meta.fields}
        if "restaurant" in field_names:
            qs = qs.filter(restaurant=restaurant)
        if "deleted_at" in field_names:
            qs = qs.filter(deleted_at__isnull=True)
        return qs

    def perform_create(self, serializer):
        kwargs = {}
        field_names = {f.name for f in serializer.Meta.model._meta.fields}
        if "restaurant" in field_names:
            kwargs["restaurant"] = self.request.restaurant
        if "created_by" in field_names:
            kwargs["created_by"] = self.request.user
        serializer.save(**kwargs)
