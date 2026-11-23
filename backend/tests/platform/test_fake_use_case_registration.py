"""Conformance test: a fake use case can be registered without the
platform depending on any specific use case implementation.

The boundary claim under test: ``simulation_platform`` does not
require a specific lane package -- a fresh use case can register itself
through :func:`simulation_platform.registry.register` and appear in
:func:`list_use_cases` alongside the existing entries.

This test file intentionally does NOT import anything from
``app.policy`` or a concrete lane adapter. RawFish lanes are published into
the registry by :func:`app.create_app`, so this file reaches them only through
the app factory seam.
"""
from __future__ import annotations

from app.simulation_platform.registry import (
    list_use_cases,
    register,
)
from app.simulation_platform.tests_helpers import build_fake_use_case
from tests._helpers import make_app


def test_fake_use_case_and_rawfish_lanes_register(monkeypatch) -> None:
    """Registering a fake use case publishes it next to RawFish lanes.

    The app factory registers product lanes as a side-effect of boot; the fake
    is then registered explicitly here. Both the built-in lane ids and the fake
    id must appear for the platform boundary to be real.
    """
    # Boot the app so the adapters publish RawFish lane ids into the
    # registry. We do not import the adapter from this file; the
    # publication must happen through the platform seam, not through
    # a direct dependency of this test on ``app.policy``.
    make_app(monkeypatch)

    fake = build_fake_use_case(uid="fake-platform-conformance")
    register(fake)

    registered = list_use_cases()
    for lane_id in ("internal_policy", "public_policy", "creator"):
        assert lane_id in registered, (
            f"expected {lane_id!r} to be registered by the app factory; "
            f"got {registered!r}"
        )
    assert "fake-platform-conformance" in registered, (
        "expected the fake use case id to appear after register(); "
        f"got {registered!r}"
    )
