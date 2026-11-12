import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_asgi = get_asgi_application()

from config.routing import websocket_urlpatterns  # noqa: E402
from apps.accounts.ws_auth import JWTAuthMiddleware  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi,
        "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
