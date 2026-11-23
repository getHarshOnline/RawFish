"""Route-surface guard for the RawFish API cutover."""
from __future__ import annotations

from tests._helpers import make_app


def _is_generic_use_case_route(route: str) -> bool:
    return route == "/api/sim/use-cases" or route.startswith("/api/sim/use-cases/")


def test_direct_sim_routes_are_not_mounted(monkeypatch) -> None:
    """RawFish keeps only the generic use-case API."""
    app = make_app(monkeypatch)
    direct_routes = frozenset(
        str(r)
        for r in app.url_map.iter_rules()
        if str(r).startswith("/api/sim/") and not _is_generic_use_case_route(str(r))
    )
    assert direct_routes == frozenset()


def test_product_does_not_mount_inherited_backend_apis(monkeypatch) -> None:
    app = make_app(monkeypatch)
    routes = frozenset(str(r) for r in app.url_map.iter_rules())
    removed_prefixes = (
        "/api/" + "graph",
        "/api/" + "simulation",
        "/api/" + "report",
    )
    assert not any(
        route.startswith(prefix)
        for route in routes
        for prefix in removed_prefixes
    )
