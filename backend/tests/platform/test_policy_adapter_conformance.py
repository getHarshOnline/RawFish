"""Conformance test: :class:`PolicyUseCase` satisfies every method of
the :class:`SimulationUseCase` Protocol with sensible values.

This is the per-method shape check that pairs with the registration
test in ``test_policy_adapter_registration.py``. The registration
test asserts the adapter publishes the right identity / catalog ids;
this test walks every Protocol method on the adapter and asserts the
return value is well-formed (non-empty catalog, all three artifact
keys present, structural ``isinstance`` against the Protocol, etc.).

Importing ``app.policy.adapter`` is expected here -- the goal is to
exercise the concrete adapter. The "platform-doesn't-need-policy"
claim is covered by the sibling test file
``test_fake_use_case_registration.py`` which intentionally avoids
``app.policy`` imports.
"""
from __future__ import annotations

from app.internal_policy.adapter import INTERNAL_POLICY_USE_CASE
from app.simulation_platform.use_case import SimulationUseCase


def test_internal_policy_use_case_satisfies_protocol_structurally() -> None:
    """``isinstance(INTERNAL_POLICY_USE_CASE, SimulationUseCase)`` passes.

    ``SimulationUseCase`` is ``@runtime_checkable``; the structural
    check here is what the registry uses internally for dispatch
    safety. If the Protocol grew a new method, this assertion would
    fail before the rest of the suite even ran.
    """
    assert isinstance(INTERNAL_POLICY_USE_CASE, SimulationUseCase)


def test_use_case_id_returns_internal_policy() -> None:
    """The identity method returns the canonical runtime lane id."""
    assert INTERNAL_POLICY_USE_CASE.use_case_id() == "internal_policy"


def test_catalog_has_personas() -> None:
    """Catalog publishes a non-empty persona list under the right id.

    The exact persona contents are pinned in the adapter-registration
    suite. Here we only assert the structural invariants the platform
    relies on: catalog id echoes ``use_case_id()`` and at least one
    persona is published. An empty catalog would be a misconfiguration
    that should surface before any per-persona check runs.
    """
    catalog = INTERNAL_POLICY_USE_CASE.catalog()
    assert catalog.use_case_id == "internal_policy"
    assert catalog.lane_manifest.lane_id == "internal_policy"
    assert len(catalog.personas) > 0, (
        "PolicyUseCase.catalog() must publish at least one persona"
    )
    for persona in catalog.personas:
        # Each PersonaSpec must carry enough metadata for the frontend
        # wizard to render the picker row; ``min_length=1`` constraints
        # in the Pydantic model already guard most of this but we
        # re-check here so a future relaxation of the model surfaces.
        assert persona.persona_id
        assert persona.label
        assert persona.cohort_question
        assert persona.segment_types_by_scope, (
            f"persona {persona.persona_id!r} must publish at least one "
            "scope key in segment_types_by_scope"
        )


def test_artifact_names_has_all_three_keys() -> None:
    """Artifact map carries the three canonical policy logical ids.

    The run-resolution layer joins each filename onto the run
    directory to locate artifacts; missing any of these three keys
    would mean a downstream lookup fails with a confusing KeyError.
    """
    names = INTERNAL_POLICY_USE_CASE.artifact_names()
    assert set(names.keys()) == {"report_json", "report_md", "sensitivity"}
    for logical_id, filename in names.items():
        assert filename, (
            f"artifact_names()[{logical_id!r}] must be a non-empty filename"
        )


def test_load_presets_returns_list() -> None:
    """``load_presets`` returns a list (possibly empty but iterable).

    The Protocol contract is "list of preset summaries"; the platform
    does not pin a per-entry shape. We only assert the return is a
    list so the frontend's iteration over presets cannot crash on a
    non-iterable.
    """
    presets = INTERNAL_POLICY_USE_CASE.load_presets()
    assert isinstance(presets, list)


def test_parse_seed_returns_dict_with_inputs_echoed() -> None:
    """``parse_seed`` returns a draft dict carrying the input fields.

    The full LLM-driven parse lives in the HTTP route; the adapter
    layer returns a deterministic skeleton so the Protocol contract
    holds without a network call. We assert the four input fields
    round-trip into the draft under their canonical keys.
    """
    draft = INTERNAL_POLICY_USE_CASE.parse_seed(
        seed_text="seed",
        stress_question="stress",
        persona="policy",
        scope="internal_org",
    )
    assert isinstance(draft, dict)
    assert draft["persona"] == "policy"
    assert draft["seed_text"] == "seed"
    assert draft["stress_question"] == "stress"
    assert draft["scope"] == "internal_org"


# ----------------------------------------------------------------------
# Adapter sensitivity passes generated_at to run_oat (purity contract)
# ----------------------------------------------------------------------


def test_run_simulation_drives_policy_runner_service() -> None:
    """The policy lane runs through the shared
    :class:`PolicyRunnerService` for the generic
    ``/api/sim/use-cases/internal_policy/simulation/<id>/start``
    dispatch. The adapter's
    ``run_simulation`` is no longer an opt-out point; instead it
    constructs the service and drives the current start lifecycle.
    The presence of ``run_simulation`` as a
    callable that is NOT a stub-raising ``NotImplementedError`` is
    the structural contract this test pins.
    """
    import inspect
    assert callable(INTERNAL_POLICY_USE_CASE.run_simulation)
    # The body must not be the legacy stub-raise; the simplest
    # check is that the source mentions the runner-service module
    # (or has more than a single ``raise NotImplementedError`` line).
    src = inspect.getsource(INTERNAL_POLICY_USE_CASE.run_simulation)
    assert "policy_runner_service" in src or "PolicyRunnerService" in src


def test_run_sensitivity_opts_out_with_not_implemented() -> None:
    """``PolicyUseCase.run_sensitivity`` opts out via
    :class:`NotImplementedError` so the generic dispatch surfaces
    501 ``CAPABILITY_UNSUPPORTED`` instead of a surrogate result.
    Real sensitivity (ensemble re-runs) is not implemented; the
    static-kernel surrogate is reachable only through
    :meth:`PolicyUseCase.run_surrogate_explainer`.
    """
    import pytest

    with pytest.raises(NotImplementedError) as excinfo:
        INTERNAL_POLICY_USE_CASE.run_sensitivity(
            simulation_id="sim_unit_test_pin",
            body={"scenario": object()},
        )
    msg = str(excinfo.value)
    # The opt-out message names the adapter method that owns the
    # static-kernel surrogate so a contributor can follow the code path.
    assert "run_surrogate_explainer" in msg


def test_run_surrogate_explainer_calls_run_oat_with_generated_at(
    monkeypatch,
) -> None:
    """The adapter's surrogate-explainer path MUST supply
    ``generated_at`` to :func:`app.policy.sensitivity.oat_runner.run_oat`
    -- the runner is a pure module that raises ``ValueError`` when
    the argument is omitted (purity contract). Pin captures the call
    kwargs so a future refactor that drops the timestamp surfaces a
    clean failure here.
    """
    from datetime import datetime

    from app.policy import sensitivity as _sens

    captured: dict = {}

    def _fake_run_oat(*args, **kwargs):
        captured.update(kwargs)
        # Return a sentinel so the adapter does not blow up on the
        # downstream model_dump call -- this is a unit pin, not an
        # end-to-end test.
        from types import SimpleNamespace

        return SimpleNamespace(model_dump=lambda mode="json": {"ok": True})

    monkeypatch.setattr(_sens.oat_runner, "run_oat", _fake_run_oat)

    # Pass scenario in body so the adapter does not try to rehydrate
    # from disk (which would require a real state.json for sim_id).
    INTERNAL_POLICY_USE_CASE.run_surrogate_explainer(
        simulation_id="sim_unit_test_pin",
        body={"scenario": object()},
    )

    assert "generated_at" in captured, (
        "PolicyUseCase.run_surrogate_explainer must pass generated_at "
        "to run_oat (the runner is pure and rejects missing timestamp)"
    )
    assert isinstance(captured["generated_at"], datetime)
