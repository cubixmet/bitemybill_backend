"""QA: free todos + alarm fields (local client scheduling)."""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import CatalogModule
from apps.billing.services import ensure_catalog_and_roles, provision_restaurant

pytestmark = pytest.mark.django_db


def _user(email: str):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    u, _ = User.objects.get_or_create(email=email, defaults={"display_name": email.split("@")[0]})
    u.set_password("demo1234")
    u.save()
    return u


def _login(email: str, restaurant_id) -> APIClient:
    client = APIClient()
    res = client.post("/api/v1/auth/login/", {"identifier": email, "password": "demo1234"}, format="json")
    assert res.status_code == 200, res.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}", HTTP_X_RESTAURANT_ID=str(restaurant_id))
    return client


@pytest.fixture
def todo_ctx():
    ensure_catalog_and_roles()
    owner = _user("qa.todo.owner@test.com")
    restaurant = provision_restaurant(owner=owner, name="QA Todo Lab")
    return {"restaurant": restaurant, "c": _login("qa.todo.owner@test.com", restaurant.id)}


def test_qa_todos_module_is_free():
    ensure_catalog_and_roles()
    mod = CatalogModule.objects.get(code="todos")
    assert mod.monthly_price == 0


def test_qa_create_complete_and_upcoming_alarms(todo_ctx):
    c = todo_ctx["c"]
    remind = (timezone.now() + timedelta(minutes=30)).isoformat()
    res = c.post(
        "/api/v1/todos/",
        {
            "title": "Check freezer",
            "notes": "Temp log",
            "priority": "high",
            "remind_at": remind,
            "alarm_enabled": True,
        },
        format="json",
    )
    assert res.status_code == 201, res.content
    todo_id = res.data["id"]
    assert res.data["alarm_enabled"] is True

    upcoming = c.get("/api/v1/todos/upcoming_alarms/")
    assert upcoming.status_code == 200
    assert any(t["id"] == todo_id for t in upcoming.data)

    done = c.post(f"/api/v1/todos/{todo_id}/complete/", {}, format="json")
    assert done.status_code == 200
    assert done.data["status"] == "done"
    assert done.data["alarm_enabled"] is False

    upcoming2 = c.get("/api/v1/todos/upcoming_alarms/")
    assert all(t["id"] != todo_id for t in upcoming2.data)


def test_qa_todos_always_provisioned(todo_ctx):
    from apps.billing.models import RestaurantModule

    assert RestaurantModule.objects.filter(
        restaurant=todo_ctx["restaurant"], module__code="todos", enabled=True
    ).exists()
