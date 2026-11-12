from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import HasRestaurantPermission
from apps.common.realtime import publish
from apps.common.viewsets import TenantModelViewSet
from apps.delivery.models import DeliveryAgent, DeliveryJob


class AgentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryAgent
        fields = ("id", "user", "name", "phone", "vehicle", "is_available")


class JobSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.name", read_only=True, default="")
    order_number = serializers.CharField(source="order.number", read_only=True)

    class Meta:
        model = DeliveryJob
        fields = ("id", "order", "order_number", "agent", "agent_name", "status", "address", "fee", "notes")


class AgentViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "delivery"
    queryset = DeliveryAgent.objects.all()
    serializer_class = AgentSerializer


class JobViewSet(TenantModelViewSet):
    permission_classes = [HasRestaurantPermission]
    permission_scope = "delivery"
    queryset = DeliveryJob.objects.select_related("order", "agent")
    serializer_class = JobSerializer

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        job = self.get_object()
        job.agent_id = request.data.get("agent")
        job.status = DeliveryJob.Status.ASSIGNED
        job.save()
        publish(f"delivery_{job.restaurant_id}", "delivery.updated", {"id": str(job.id), "status": job.status})
        publish(f"order_{job.order_id}", "order.updated", {"delivery_status": job.status})
        return Response(JobSerializer(job).data)

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        job = self.get_object()
        job.status = request.data.get("status", job.status)
        job.save(update_fields=["status"])
        publish(f"order_{job.order_id}", "order.updated", {"delivery_status": job.status})
        return Response(JobSerializer(job).data)
