"""Activity log pagination regression tests."""
import pytest
from rest_framework.test import APIClient

from apps.billing.services import ensure_catalog_and_roles, provision_restaurant

pytestmark = pytest.mark.django_db


def _owner_client():
    from django.contrib.auth import get_user_model

    from apps.audit.models import AuditLog

    ensure_catalog_and_roles()
    User = get_user_model()
    owner, _ = User.objects.get_or_create(
        email="audit.owner@test.com", defaults={"display_name": "Audit Owner"}
    )
    owner.set_password("demo1234")
    owner.save()
    restaurant = provision_restaurant(owner=owner, name="Audit Lab")
    for i in range(55):
        AuditLog.objects.create(
            restaurant=restaurant,
            actor=owner,
            action=f"order.created" if i % 2 == 0 else f"expense.approved",
            object_type="order" if i % 2 == 0 else "expense",
            object_id=str(i),
            payload={"n": i},
        )
    client = APIClient()
    res = client.post(
        "/api/v1/auth/login/",
        {"identifier": "audit.owner@test.com", "password": "demo1234"},
        format="json",
    )
    assert res.status_code == 200
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {res.data['access']}",
        HTTP_X_RESTAURANT_ID=str(restaurant.id),
    )
    return client, restaurant


def test_audit_list_is_paginated_and_capped():
    client, _ = _owner_client()
    res = client.get("/api/v1/audit/?page=1&page_size=20")
    assert res.status_code == 200
    assert res.data["count"] >= 55
    assert res.data["page"] == 1
    assert res.data["page_size"] == 20
    assert len(res.data["results"]) == 20
    assert res.data["next"]
    assert res.data["previous"] is None

    page2 = client.get("/api/v1/audit/?page=2&page_size=20")
    assert page2.status_code == 200
    assert len(page2.data["results"]) == 20
    assert page2.data["previous"]
    ids1 = {r["id"] for r in res.data["results"]}
    ids2 = {r["id"] for r in page2.data["results"]}
    assert ids1.isdisjoint(ids2)

    capped = client.get("/api/v1/audit/?page_size=999")
    assert capped.status_code == 200
    assert capped.data["page_size"] == 100
    assert len(capped.data["results"]) <= 100


def test_audit_search_and_unauth():
    client, _ = _owner_client()
    res = client.get("/api/v1/audit/?search=expense&page_size=50")
    assert res.status_code == 200
    assert all("expense" in (r["action"] + r["object_type"]).lower() for r in res.data["results"])

    bare = APIClient()
    deny = bare.get("/api/v1/audit/")
    assert deny.status_code in (401, 403)
