from django.urls import path

from apps.orders.consumers import OperationsConsumer

websocket_urlpatterns = [
    path("ws/ops/", OperationsConsumer.as_asgi()),
]
