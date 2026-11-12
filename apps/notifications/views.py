from rest_framework import serializers

from apps.common.permissions import HasRestaurantPermission
from apps.common.viewsets import TenantModelViewSet
from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "title", "body", "kind", "is_read", "created_at")


class NotificationViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
