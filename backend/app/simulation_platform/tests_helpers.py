"""Test helpers for the simulation-platform package.

Lives in the package (not under ``tests/``) so conformance suites in
both the backend test tree and any future second consumer (frontend
contract tests via pytest, downstream integrators) can share the same
fake-use-case builder without duplicating the wiring.

The helper produces a :class:`FakeUseCase` that satisfies the
:class:`SimulationUseCase` structural Protocol with deterministic,
side-effect-free implementations. Every method returns the simplest
shape that satisfies the contract; tests that need richer behaviour
should subclass and override the specific methods they exercise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import CatalogPayload, PersonaSpec, build_minimal_lane_manifest
from .guardrails import GuardrailCapabilities
from .use_case import SimulationUseCase


@dataclass
class FakeUseCase:
    """In-memory fake satisfying :class:`SimulationUseCase` structurally.

    Holds an id and an optional catalog override so a single test can
    spin up several fakes with different identities. ``parse_seed``,
    ``validate_scenario``, ``generate_report`` etc. return inert
    dict/None values -- conformance tests assert the shape; behaviour
    tests should subclass and override.
    """

    uid: str = "fake"
    catalog_payload: Optional[CatalogPayload] = None
    prepare_artifacts_written: List[Path] = field(default_factory=list)
    run_simulation_called_with: List[Path] = field(default_factory=list)

    #: No canonical report schema; the fake echoes payloads. The
    #: Protocol requires the attribute be present even when None so
    #: the generic dispatch's ``hasattr / model_validate`` check has a
    #: stable miss path.
    report_schema: Optional[type] = None

    #: No draft / provenance-merge surface on the fake either; the
    #: structured-output wrapper falls back to :attr:`report_schema`
    #: (also ``None``) so a fake-driven test runs the loose-JSON
    #: path. Declared so the Protocol's structural check has a
    #: stable miss path.
    report_draft_schema: Optional[type] = None

    #: Mirrors the optional ``prepare_manifest_schema`` slot the
    #: Protocol carries on the write side. Fakes declare ``None`` so
    #: structural-conformance ``isinstance(FakeUseCase(), SimulationUseCase)``
    #: checks still pass after the Protocol grew the slot.
    prepare_manifest_schema: Optional[type] = None

    #: No guardrail hooks; opt-in seam is empty on the fake. Tests
    #: that want to exercise the dispatch layer's guardrail-hook
    #: wiring subclass and override this with a populated instance.
    guardrail_capabilities: Optional[GuardrailCapabilities] = None

    #: The fake's ``generate_report`` is a pure echo with no
    #: guardrail side effects, so the dispatch layer's post-generate
    #: hook chain runs unchanged on it. Declared as ``False`` so the
    #: Protocol's structural check has a stable miss path and so
    #: subclasses that want to opt into the skip can override.
    adapter_runs_guardrails_internally: bool = False

    def use_case_id(self) -> str:
        return self.uid

    def catalog(self) -> CatalogPayload:
        # Default catalog with a single persona / no scopes so callers
        # that just need "any well-formed CatalogPayload" don't have to
        # build one themselves.
        if self.catalog_payload is not None:
            return self.catalog_payload
        return CatalogPayload(
            use_case_id=self.uid,
            lane_manifest=build_minimal_lane_manifest(
                lane_id=self.uid,
                label="Fake Lane",
                summary="Test-only lane manifest",
            ),
            personas=[
                PersonaSpec(
                    persona_id="default",
                    label="Default Persona",
                    cohort_question="Who is the audience?",
                    scopes=None,
                    segment_types_by_scope={None: ["generic"]},
                    defaults={},
                    report_sections_by_scope={None: ["summary"]},
                ),
            ],
        )

    def validate_scenario(self, payload: Any) -> Any:
        # Echo the payload so tests can assert round-tripping without
        # asserting any particular validator behaviour.
        return payload

    def rehydrate_scenario(self, state_dict: Dict[str, Any]) -> Any:
        # The fake does not own a scenario shape; return the persisted
        # ``scenario`` sub-dict unchanged so the platform's
        # registry-dispatched rehydration path has an honest no-op
        # adapter to test against.
        return state_dict.get("scenario", state_dict)

    def parse_seed(
        self,
        seed_text: str,
        stress_question: str,
        persona: str,
        scope: Optional[str],
    ) -> Any:
        return {
            "seed_text": seed_text,
            "stress_question": stress_question,
            "persona": persona,
            "scope": scope,
        }

    def load_presets(self) -> List[Any]:
        return []

    def build_ontology(self, scenario: Any, run_dir: Path) -> None:
        # No-op: real use cases hit the audience store; the fake just
        # records the call site so tests can assert it ran.
        self.prepare_artifacts_written.append(run_dir)

    def write_prepare_artifacts(self, scenario: Any, run_dir: Path) -> Any:
        self.prepare_artifacts_written.append(run_dir)
        return {"artifacts": {}}

    def generate_report(
        self,
        scenario: Any,
        run_metadata: Any,
        evidence_catalog: Any,
        llm_client: Any = None,
    ) -> Any:
        del llm_client  # fake does not call the LLM; signature parity only
        return {"report": {}, "scenario": scenario}

    def artifact_names(self) -> Dict[str, str]:
        return {
            "report_json": "fake_report.json",
            "manifest": "fake_manifest.json",
        }

    def run_simulation(self, scenario: Any, run_dir: Path) -> None:
        # Deterministic-mock runner: records the call site and writes
        # a minimal ``run_state.json`` marker so the generic dispatch
        # layer sees the run as finished. Subclasses that want to pin
        # a specific runner failure mode override and raise.
        del scenario
        self.run_simulation_called_with.append(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_state.json").write_text(
            '{"use_case_id": "' + self.uid + '", "status": "completed", '
            '"runner": "fake_use_case_mock"}',
            encoding="utf-8",
        )

    def run_sensitivity(self, simulation_id: str, body: Any) -> Any:
        # Default: signal "not supported"; conformance tests pin this
        # as the canonical opt-out for use cases that lack sensitivity.
        raise NotImplementedError(
            "FakeUseCase does not implement run_sensitivity by default"
        )


def build_fake_use_case(
    uid: str = "fake",
    catalog_payload: Optional[CatalogPayload] = None,
) -> SimulationUseCase:
    """Construct a :class:`FakeUseCase` typed as the Protocol.

    Tests should prefer this constructor over instantiating
    :class:`FakeUseCase` directly when they want to assert "the value
    satisfies :class:`SimulationUseCase`" -- the return annotation
    makes that intent explicit at the call site.
    """

    return FakeUseCase(uid=uid, catalog_payload=catalog_payload)


__all__ = [
    "FakeUseCase",
    "build_fake_use_case",
]
