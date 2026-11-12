from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.common.permissions import HasRestaurantAccess


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class AuditListView(APIView):
    """
    Paginated activity / audit log.

    Query params:
      - page (1-based, default 1)
      - page_size (default 25, max 100)
      - search (optional; matches action, object_type, actor email)
      - action (optional exact/prefix filter)
    """

    permission_classes = [HasRestaurantAccess]

    def get(self, request):
        qs = AuditLog.objects.select_related("actor").order_by("-created_at")
        if getattr(request.user, "user_kind", None) != "platform":
            qs = qs.filter(restaurant=request.restaurant)

        action = (request.query_params.get("action") or "").strip()
        if action:
            qs = qs.filter(action__icontains=action)

        search = (request.query_params.get("search") or "").strip()
        if search:
            from django.db.models import Q

            qs = qs.filter(
                Q(action__icontains=search)
                | Q(object_type__icontains=search)
                | Q(object_id__icontains=search)
                | Q(actor__email__icontains=search)
                | Q(actor__display_name__icontains=search)
            )

        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get("page_size") or DEFAULT_PAGE_SIZE)
        except (TypeError, ValueError):
            page_size = DEFAULT_PAGE_SIZE
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))

        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        rows = list(qs[start:end])

        def abs_url(p: int | None) -> str | None:
            if p is None:
                return None
            q = request.query_params.copy()
            q["page"] = str(p)
            q["page_size"] = str(page_size)
            return request.build_absolute_uri(f"{request.path}?{q.urlencode()}")

        has_next = end < total
        has_prev = page > 1 and total > 0
        next_page = page + 1 if has_next else None
        prev_page = page - 1 if has_prev else None

        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "total_pages": max(1, (total + page_size - 1) // page_size) if total else 0,
                "next": abs_url(next_page),
                "previous": abs_url(prev_page),
                "results": [
                    {
                        "id": str(a.id),
                        "action": a.action,
                        "object_type": a.object_type,
                        "object_id": a.object_id,
                        "payload": a.payload,
                        "actor": getattr(a.actor, "email", None),
                        "actor_name": getattr(a.actor, "display_name", None)
                        or getattr(a.actor, "email", None),
                        "created_at": a.created_at,
                    }
                    for a in rows
                ],
            }
        )
