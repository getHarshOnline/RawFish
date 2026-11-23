"""MockZep determinism + factory ZEP_MODE strictness.

Pins: identical ``(scenario, master_seed, simulation_id)`` produce
byte-identical entity sets; entity uuids are uuid5-keyed on
``scenario_hash`` (not ``simulation_id``) so two sims of the same
scenario share uuids; changing scenario content changes uuids;
:meth:`MockZepStorage.get_factory` strict-validates ``ZEP_MODE`` and
raises ``RuntimeError`` on unknown values; ``ZEP_MODE=mock`` boots
without ``ZEP_API_KEY``.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.policy.schema import Scenario
from app.seam.zep_mock import MockZepStorage, resolve_master_seed, scenario_hash


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _minimal_segment(
    *,
    segment_id: str = "s_fans",
    seg_type: str = "follower_cohort",
    name: str = "Fans cohort",
) -> Dict[str, Any]:
    return {
        "segment_id": segment_id,
        "name": name,
        "type": seg_type,
        "influence_level": 3,
        "vulnerability_level": 3,
        "compliance_propensity": 3,
        "trust_level": 3,
        "mobilization_capacity": 3,
        "what_changes_their_mind": ["new evidence emerges"],
    }


def _minimal_scenario_dict(**overrides: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "persona": "creator",
        "title": "Det scenario",
        "summary": "Deterministic creator scenario for mock-zep tests.",
        "seed_text": "Post 1/1: deterministic seed text.",
        "stress_question": "Where might this misfire?",
        "stakeholders": [_minimal_segment()],
        "assumptions": ["author is in good standing"],
        "success_criteria": ["no factual correction required"],
        "failure_modes_to_watch": ["misread out of context"],
        "drift_horizon_rounds": 1,
        "run": {"ensemble_n": 1, "run_seed": 42},
    }
    data.update(overrides)
    return data


def _build_scenario(**overrides: Any) -> Scenario:
    return Scenario.model_validate(
        _minimal_scenario_dict(**overrides),
        context={"allow_expensive": False},
    )


# ---------------------------------------------------------------------------
# uuid namespace driven by scenario_hash (not simulation_id)
# ---------------------------------------------------------------------------


def test_seed_uuids_derived_from_scenario_hash_not_sim_id():
    """Same scenario, two sim_ids → identical entity uuids."""
    scenario = _build_scenario()
    store_a = MockZepStorage()
    store_b = MockZepStorage()
    store_a.seed_from_scenario("sim_a", scenario)
    store_b.seed_from_scenario("sim_b_completely_different_id", scenario)

    uuids_a = [e.uuid for e in store_a._store["sim_a"]]
    uuids_b = [e.uuid for e in store_b._store["sim_b_completely_different_id"]]
    assert uuids_a == uuids_b
    # And the scenario_hash recorded on each instance matches.
    assert store_a.scenario_hash == store_b.scenario_hash
    # And the master_seed derived deterministically (no env overrides set).
    assert store_a.master_seed == store_b.master_seed
    assert store_a.seed_zep == store_b.seed_zep


def test_seed_uuids_change_with_scenario_content():
    """Different stakeholder content → different uuids (no cache-collision)."""
    scen_default = _build_scenario()
    # Mutate the stakeholder name -- everything else identical.
    different_stake = _minimal_segment(name="DIFFERENT cohort name")
    scen_changed = _build_scenario(stakeholders=[different_stake])

    store_default = MockZepStorage()
    store_changed = MockZepStorage()
    store_default.seed_from_scenario("sim_x", scen_default)
    store_changed.seed_from_scenario("sim_x", scen_changed)

    assert (
        store_default.scenario_hash != store_changed.scenario_hash
    ), "scenario_hash must change when stakeholder content changes"
    uuids_default = [e.uuid for e in store_default._store["sim_x"]]
    uuids_changed = [e.uuid for e in store_changed._store["sim_x"]]
    assert uuids_default != uuids_changed


def test_scenario_hash_excludes_run_block():
    """Mutating scenario.run must NOT change the uuid set.

    ``run.run_seed`` / ``run.ensemble_n`` are variance-knobs by design;
    including them in the hash would defeat re-prepare determinism.
    """
    base = _build_scenario()
    bumped = _build_scenario(run={"ensemble_n": 1, "run_seed": 9999})
    assert scenario_hash(base) == scenario_hash(bumped)


def test_master_seed_resolution_env_precedence(monkeypatch):
    """RAWFISH_RUN_SEED > POLICY_RANDOM_SEED > scenario_hash[:16]."""
    scen = _build_scenario()
    scen_h = scenario_hash(scen)

    # All env unset → scenario-hash fallback.
    monkeypatch.delenv("RAWFISH_RUN_SEED", raising=False)
    monkeypatch.delenv("POLICY_RANDOM_SEED", raising=False)
    assert resolve_master_seed(scen_h, None) == int(scen_h[:16], 16)

    # POLICY_RANDOM_SEED set → use it.
    monkeypatch.setenv("POLICY_RANDOM_SEED", "1234")
    assert resolve_master_seed(scen_h, None) == 1234

    # RAWFISH_RUN_SEED takes precedence.
    monkeypatch.setenv("RAWFISH_RUN_SEED", "9999")
    assert resolve_master_seed(scen_h, None) == 9999

    # Explicit seed beats both env vars.
    assert resolve_master_seed(scen_h, 7) == 7


def test_explicit_seed_keyword_argument():
    """``seed=`` keyword on the constructor wins over env-resolution."""
    scen = _build_scenario()
    store = MockZepStorage(seed=42)
    store.seed_from_scenario("sim_q", scen)
    assert store.master_seed == 42


# ---------------------------------------------------------------------------
# Factory strict-validates ZEP_MODE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bogus_mode", ["production", "", "mockmock", "1"])
def test_factory_rejects_bogus_zep_mode(monkeypatch, bogus_mode):
    """Any ZEP_MODE outside {mock, real} must RuntimeError loudly.

    Values listed here MUST fail at parse-time. Whitespace/case normalization
    (e.g. ``"REAL "`` -> ``"real"``) is intentional and is tested separately
    in ``test_factory_normalizes_real_with_whitespace_and_case`` -- do not
    add normalize-to-valid inputs to this parametrize list.
    """
    monkeypatch.setenv("ZEP_MODE", bogus_mode)
    with pytest.raises(RuntimeError, match="ZEP_MODE"):
        MockZepStorage.get_factory()


def test_factory_normalizes_real_with_whitespace_and_case(monkeypatch):
    """``parse_zep_mode`` strips + lowercases, so ``"REAL "`` -> ``"real"``.

    With ZEP_API_KEY set, this normalization must yield a real-mode factory
    rather than raising ``RuntimeError`` -- whitespace/case insensitivity is
    intentional contract surface, not a parse bug. Asserts the normalize
    path explicitly instead of via the (wrong-reason) "missing ZEP_API_KEY"
    fallback in the bogus_mode parametrize.
    """
    monkeypatch.setenv("ZEP_MODE", "REAL ")
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    # get_factory may construct a real ZepClient (or raise an import error
    # depending on optional deps); the contract under test is that ZEP_MODE
    # was NOT rejected as bogus. Any RuntimeError mentioning "ZEP_MODE"
    # would indicate the normalize step failed.
    try:
        client = MockZepStorage.get_factory()
    except RuntimeError as exc:  # pragma: no cover -- guarded below
        assert "ZEP_MODE" not in str(exc), (
            f"'REAL ' must normalize to 'real', got RuntimeError: {exc}"
        )
        # Re-raise non-ZEP_MODE RuntimeErrors so a real regression surfaces.
        raise
    else:
        # Real-mode factory must not return a MockZepStorage instance.
        assert not isinstance(client, MockZepStorage), (
            "ZEP_MODE='REAL ' normalized but factory returned mock client"
        )


def test_factory_returns_mock_instance_for_mock_mode(monkeypatch):
    monkeypatch.setenv("ZEP_MODE", "mock")
    monkeypatch.delenv("ZEP_API_KEY", raising=False)
    client = MockZepStorage.get_factory()
    assert isinstance(client, MockZepStorage)


def test_factory_requires_zep_api_key_for_real_mode(monkeypatch):
    """ZEP_MODE=real without ZEP_API_KEY → RuntimeError; no network attempt."""
    monkeypatch.setenv("ZEP_MODE", "real")
    monkeypatch.delenv("ZEP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ZEP_API_KEY"):
        MockZepStorage.get_factory()


# ---------------------------------------------------------------------------
# Config.validate accepts ZEP_MODE=mock without ZEP_API_KEY
# ---------------------------------------------------------------------------


def test_config_validate_accepts_mock_without_zep_key(monkeypatch):
    """ZEP_MODE=mock must boot without ZEP_API_KEY.

    Importable Config + create_app under a clean env that satisfies every
    other validator but deliberately lacks ZEP_API_KEY. Boot must succeed.
    """
    # Mirror tests/_helpers _set_dummy_env but DROP ZEP_API_KEY.
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("FLASK_DEBUG", "true")
    monkeypatch.setenv("SECRET_KEY", "test-secret-32chars-test-secret-32")
    monkeypatch.setenv("ZEP_MODE", "mock")
    monkeypatch.delenv("ZEP_API_KEY", raising=False)
    import importlib
    import sys
    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])
    from app.config import Config
    # Should NOT raise -- mock mode does not require a Zep key.
    Config.validate()
