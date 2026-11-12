from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.audit import log_action
from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.todos.models import Todo


class TodoSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.SerializerMethodField()

    class Meta:
        model = Todo
        fields = (
            "id",
            "title",
            "notes",
            "status",
            "priority",
            "due_at",
            "remind_at",
            "alarm_enabled",
            "completed_at",
            "assigned_to",
            "assigned_to_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("completed_at", "created_at", "updated_at")

    def get_assigned_to_name(self, obj):
        u = obj.assigned_to
        if not u:
            return ""
        return getattr(u, "display_name", None) or getattr(u, "email", "") or str(u.pk)


class TodoViewSet(TenantModelViewSet):
    """
    Free restaurant todos. Alarm scheduling is done on-device (shell Notification API /
    Expo local notifications) — the API only stores remind_at; no third-party push.
    """

    permission_classes = [HasRestaurantPermission]
    permission_scope = "todos"
    queryset = Todo.objects.select_related("assigned_to")
    serializer_class = TodoSerializer
    filterset_fields = ["status", "priority", "alarm_enabled"]
    search_fields = ["title", "notes"]

    def perform_create(self, serializer):
        kwargs = {"restaurant": self.request.restaurant, "created_by": self.request.user}
        if not serializer.validated_data.get("assigned_to"):
            kwargs["assigned_to"] = self.request.user
        serializer.save(**kwargs)
        log_action(self.request, "todos.created", serializer.instance)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        todo = self.get_object()
        todo.status = Todo.Status.DONE
        todo.completed_at = timezone.now()
        todo.alarm_enabled = False
        todo.save(update_fields=["status", "completed_at", "alarm_enabled", "updated_at"])
        return Response(TodoSerializer(todo).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        todo = self.get_object()
        todo.status = Todo.Status.OPEN
        todo.completed_at = None
        todo.save(update_fields=["status", "completed_at", "updated_at"])
        return Response(TodoSerializer(todo).data)

    @action(detail=False, methods=["get"])
    def upcoming_alarms(self, request):
        """Open todos with a future (or recent) remind_at — clients re-arm local alarms from this."""
        now = timezone.now()
        qs = (
            self.get_queryset()
            .filter(status=Todo.Status.OPEN, alarm_enabled=True, remind_at__isnull=False)
            .filter(remind_at__gte=now - timedelta(hours=1))
            .order_by("remind_at")[:100]
        )
        return Response(TodoSerializer(qs, many=True).data)
