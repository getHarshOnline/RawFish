"""Policy package: lane-private scenario schema, persona enum, and defaults.

Re-exports the public surface that the API layer and runner consume:
:class:`Scenario` + nested models, :class:`Persona`,
:data:`PERSONA_SEGMENT_TYPES`, scale caps, and the
:class:`HypothesisTag` / :class:`ExpectedRiskTag` taxonomies. Callers
import from :mod:`app.policy` rather than reaching into the submodules.
"""

from .schema import (
    AudienceSegment,
    AudienceSegmentPriors,
    Claim,
    Clause,
    DriftParams,
    DriverHint,
    EvidenceRef,
    JSONPatchOp,
    OceanScores,
    Persona,
    PriorDistribution,
    PriorProvenance,
    Report,
    RunControls,
    Scenario,
    SchwartzScores,
    Section,
    TraitMap,
    Variant,
    VariantKind,
    PERSONA_SEGMENT_TYPES,
    SCALE_CAP_LARGE,
    SCALE_CAP_POC,
)
from .scenario_loader import (
    ScenarioValidationError,
    load_scenario,
    load_scenario_dict,
    load_scenario_from_yaml,
    load_scenario_from_dict,
)
from .hypothesis_tags import (
    HypothesisTag,
    CANONICAL_HYPOTHESIS_TAGS,
    ExpectedRiskTag,
    CANONICAL_EXPECTED_RISK_TAGS,
)

__all__ = [
    "AudienceSegment",
    "AudienceSegmentPriors",
    "Claim",
    "Clause",
    "DriftParams",
    "DriverHint",
    "EvidenceRef",
    "JSONPatchOp",
    "OceanScores",
    "Persona",
    "PriorDistribution",
    "PriorProvenance",
    "Report",
    "RunControls",
    "Scenario",
    "SchwartzScores",
    "Section",
    "TraitMap",
    "Variant",
    "VariantKind",
    "PERSONA_SEGMENT_TYPES",
    "SCALE_CAP_LARGE",
    "SCALE_CAP_POC",
    "ScenarioValidationError",
    "load_scenario",
    "load_scenario_dict",
    "load_scenario_from_yaml",
    "load_scenario_from_dict",
    "HypothesisTag",
    "CANONICAL_HYPOTHESIS_TAGS",
    "ExpectedRiskTag",
    "CANONICAL_EXPECTED_RISK_TAGS",
]
