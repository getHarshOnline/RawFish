"""Pins the generic blueprint's :func:`_err` releases the idempotency
claim sentinel on every non-2xx path -- mirror of the policy
blueprint's behaviour.

Without the release, a first attempt that errors with the SAME
Idempotency-Key would block legitimate retries (with a corrected
body) for the full ``_CLAIM_POLL_*`` wait window because the
in-flight claim sentinel remains on disk. The policy lane has had
this contract since the idempotency seam shipped; the generic
``/api/sim/use-cases/*`` blueprint inherited the same contract via
this fix.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.tests_helpers import build_fake_use_case
from tests._helpers import make_app


@pytest.fixture
def app(monkeypatch):
    return make_app(monkeypatch, AUTH_ENABLED="false")


def _registry_snapshot() -> dict:
    return dict(platform_registry._REGISTRY)  # type: ignore[attr-defined]


def _registry_restore(snap: dict) -> None:
    platform_registry._REGISTRY.clear()  # type: ignore[attr-defined]
    platform_registry._REGISTRY.update(snap)  # type: ignore[attr-defined]


def test_generic_err_releases_claim_on_non_2xx(app, tmp_path):
    """First attempt errors with INVALID_INPUT (missing scenario);
    a same-key retry with a corrected body MUST succeed (201) rather
    than 409 IDEMPOTENCY_IN_PROGRESS. The pin proves the claim
    sentinel was released on the first attempt's error path.
    """
    snapshot = _registry_snapshot()
    try:
        platform_registry.register(build_fake_use_case(uid="claim_release_lane"))
        client = app.test_client()
        # Use a uuid4-derived key per test run so we never collide
        # with a prior run's persisted idempotency cache files (which
        # live under Config.OASIS_SIMULATION_DATA_DIR). The pin is
        # about CONTRACT behaviour, not file-isolation semantics.
        key = f"mf_claim_release_test_{uuid.uuid4().hex}"
        headers = {"Idempotency-Key": key, "Content-Type": "application/json"}

        # First attempt: trigger INVALID_INPUT (no `scenario` key) which
        # hits the generic blueprint's _err on a 400 path.
        bad = client.post(
            "/api/sim/use-cases/claim_release_lane/simulation/create",
            data=json.dumps({"not_a_scenario_key": True}),
            headers=headers,
        )
        assert bad.status_code == 400, bad.get_data(as_text=True)
        err = bad.get_json()["error"]
        assert err["code"] == "INVALID_INPUT"

        # Same-key retry with a CORRECTED body. If the claim sentinel
        # from the first attempt was NOT released, this either returns
        # 409 (in-flight) or 422 (key mismatch); both indicate the
        # leak. We want a clean 201 (the create proceeds).
        good = client.post(
            "/api/sim/use-cases/claim_release_lane/simulation/create",
            data=json.dumps({"scenario": {"persona": "default"}}),
            headers=headers,
        )
        assert good.status_code == 201, good.get_data(as_text=True)
    finally:
        _registry_restore(snapshot)
