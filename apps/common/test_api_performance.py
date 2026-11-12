"""
API performance baselines: query counts + response snapshots.

Run: pytest apps/common/tests_api_performance.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.test.utils import override_settings

from apps.common.perf_harness import (
    PERF_GET_ENDPOINTS,
    contract_shape,
    seed_perf_restaurant,
    snapshot_get,
)

pytestmark = pytest.mark.django_db

BASELINE_DIR = Path(__file__).resolve().parent / "_perf_baselines"
# Soft ceilings AFTER optimization (measured + headroom). Fail CI if N+1 returns.
QUERY_BUDGETS: dict[str, int] = {
    "/reports/overview/": 25,
    "/accounting/balance-sheet/": 14,
    "/menu/categories/": 13,
    "/accounting/gst/?from=2020-01-01&to=2030-12-31": 13,
    "/menu/items/": 12,
    "/orders/": 12,
    "/accounting/journals/": 12,
    "/accounting/trial-balance/": 12,
    "/accounting/profit-and-loss/?from=2020-01-01&to=2030-12-31": 12,
    "/customers/": 11,
    "/expenses/categories/": 11,
    "/inventory/suppliers/": 10,
    "/audit/": 10,
    "/production/recipes/": 10,
}
# Pre/post summaries: apps/common/_perf_baselines/{before,after}_optimize_summary.json


@pytest.fixture
def perf_ctx():
    return seed_perf_restaurant()


@override_settings(DEBUG=True)
def test_perf_capture_and_budget(perf_ctx, tmp_path):
    """Capture snapshots + query counts; enforce budgets on hot endpoints."""
    client = perf_ctx["client"]
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for rel in PERF_GET_ENDPOINTS:
        path = f"/api/v1{rel}"
        status, body, queries, ms = snapshot_get(client, path)
        results.append({"path": rel, "status": status, "queries": queries, "ms": round(ms, 2)})
        # Persist structural contract (UUID/datetime-normalized) for regression
        snap_file = BASELINE_DIR / (rel.strip("/").replace("/", "__").replace("?", "_") + ".json")
        shape = {"status": status, "shape": contract_shape(body)}
        if snap_file.exists():
            prev = json.loads(snap_file.read_text())
            assert prev["status"] == status, f"{rel} status changed {prev['status']} → {status}"
            # Support migrating from raw-body snapshots to shape snapshots once.
            prev_shape = prev.get("shape", contract_shape(prev.get("body")))
            assert prev_shape == shape["shape"], f"{rel} response contract shape changed"
        else:
            snap_file.write_text(json.dumps(shape, indent=2, default=str))
        budget = QUERY_BUDGETS.get(rel)
        if budget is not None and status == 200:
            assert queries <= budget, f"{rel} used {queries} queries (budget {budget})"

    # Always write a summary artifact for the report
    summary = sorted(results, key=lambda r: (-r["queries"], -r["ms"]))
    (BASELINE_DIR / "last_run_summary.json").write_text(json.dumps(summary, indent=2))
    print("\n=== PERF SUMMARY (worst first) ===")
    for row in summary[:15]:
        print(f"{row['queries']:4d}q  {row['ms']:8.1f}ms  {row['status']}  {row['path']}")
