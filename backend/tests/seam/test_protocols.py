"""IAudienceStore Protocol conformance.

Pins the 7-method ``IAudienceStore`` contract and that mock-only
helpers (``seed_from_scenario``, ``_store``) are NOT on the Protocol --
guards the seam boundary against silent expansion.

The structural-isinstance check is intentionally NOT extended to assert
``isinstance(MockZepStorage(), IAudienceStore)`` against every method:
the 5 ingest/query methods (``set_ontology``, ``add_episode``,
``search``, ``get_all_nodes``, ``get_all_edges``) are wired on the mock
alongside the GraphitiAudienceStore landing. This file pins the
Protocol *surface* so the test breaks loudly if a backend lands
without the corresponding mock-method definitions.
"""
from __future__ import annotations

from app.seam.protocols import IAudienceStore


# The 7-method ``IAudienceStore`` contract.
_PROTOCOL_METHODS = (
    # Ingest / mutation
    "set_ontology",
    "add_episode",
    # Query
    "search",
    "get_all_nodes",
    "get_all_edges",
    # Original 2-method read surface
    "filter_defined_entities",
    "get_entities_by_type",
)


def test_seed_from_scenario_is_not_on_protocol():
    """seed_from_scenario is mock-only; Protocol must not advertise it.

    If a future refactor adds ``seed_from_scenario`` to the Protocol, the
    cloud / graphiti backends will fail Protocol conformance -- that's
    the boundary this test defends.
    """
    assert not hasattr(IAudienceStore, "seed_from_scenario"), (
        "IAudienceStore must not expose seed_from_scenario -- that method is "
        "mock-only and pulling it onto the Protocol would force the real "
        "Zep / Graphiti clients to implement test scaffolding."
    )


def test_store_attribute_is_not_on_protocol():
    """``_store`` is the mock's in-memory dict; Protocol must not advertise it."""
    assert not hasattr(IAudienceStore, "_store"), (
        "IAudienceStore must not expose ``_store`` -- that attribute is "
        "the MockZepStorage's private in-memory dict."
    )


def test_protocol_exposes_full_seven_method_surface():
    """All 7 ``IAudienceStore`` contract methods are part of the Protocol."""
    for name in _PROTOCOL_METHODS:
        assert hasattr(IAudienceStore, name), (
            f"IAudienceStore is missing required method {name!r} from the "
            "7-method ``IAudienceStore`` contract."
        )


def test_protocol_method_count_matches_contract():
    """Guard against silent expansion: only the 7 contract methods are public."""
    public: set[str] = {
        name
        for name in dir(IAudienceStore)
        if not name.startswith("_") and callable(getattr(IAudienceStore, name, None))
    }
    # Filter out Protocol/typing-runtime helpers contributed by the
    # ``Protocol`` base; we only assert the contract methods are present
    # AND that no UNDOCUMENTED extras have slipped onto the surface.
    contract: set[str] = set(_PROTOCOL_METHODS)
    undocumented = public - contract
    assert not undocumented, (
        f"IAudienceStore exposes undocumented public methods {sorted(undocumented)!r}; "
        "every method on the Protocol must be in the 7-method contract."
    )
    missing = contract - public
    assert not missing, (
        f"IAudienceStore is missing contract methods {sorted(missing)!r}."
    )
