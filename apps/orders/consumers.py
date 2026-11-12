from urllib.parse import parse_qs

from channels.generic.websocket import AsyncJsonWebsocketConsumer


class OperationsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close()
            return
        query = parse_qs(self.scope.get("query_string", b"").decode())
        restaurant_id = (query.get("restaurant") or [None])[0]
        order_id = (query.get("order") or [None])[0]
        session_id = (query.get("session") or [None])[0]
        self.groups_joined = []
        if restaurant_id:
            if user.user_kind != "platform":
                from apps.restaurants.models import RestaurantMembership
                from channels.db import database_sync_to_async

                ok = await database_sync_to_async(
                    RestaurantMembership.objects.filter(
                        user=user, restaurant_id=restaurant_id, is_active=True
                    ).exists
                )()
                if not ok:
                    await self.close()
                    return
            for name in (f"restaurant_{restaurant_id}", f"kitchen_{restaurant_id}", f"orders_{restaurant_id}"):
                await self.channel_layer.group_add(name, self.channel_name)
                self.groups_joined.append(name)
        if order_id:
            from apps.orders.models import Order
            from channels.db import database_sync_to_async

            async def order_ok():
                order = await database_sync_to_async(
                    lambda: Order.objects.filter(pk=order_id).select_related("restaurant").first()
                )()
                if not order:
                    return False
                if user.user_kind == "platform" or str(order.created_by_id) == str(user.id):
                    return True
                from apps.restaurants.models import RestaurantMembership

                return await database_sync_to_async(
                    RestaurantMembership.objects.filter(
                        user=user, restaurant_id=order.restaurant_id, is_active=True
                    ).exists
                )()

            if not await order_ok():
                await self.close()
                return
            await self.channel_layer.group_add(f"order_{order_id}", self.channel_name)
            self.groups_joined.append(f"order_{order_id}")
        if session_id:
            from apps.orders.models import TableSession
            from channels.db import database_sync_to_async

            async def session_ok():
                sess = await database_sync_to_async(
                    lambda: TableSession.objects.filter(pk=session_id).first()
                )()
                if not sess:
                    return False
                if user.user_kind == "platform":
                    return True
                from apps.restaurants.models import RestaurantMembership

                return await database_sync_to_async(
                    RestaurantMembership.objects.filter(
                        user=user, restaurant_id=sess.restaurant_id, is_active=True
                    ).exists
                )()

            if not await session_ok():
                await self.close()
                return
            await self.channel_layer.group_add(f"table_session_{session_id}", self.channel_name)
            self.groups_joined.append(f"table_session_{session_id}")
        await self.accept()

    async def disconnect(self, code):
        for name in getattr(self, "groups_joined", []):
            await self.channel_layer.group_discard(name, self.channel_name)

    async def ops_event(self, event):
        await self.send_json({"event": event["event"], "payload": event.get("payload")})
