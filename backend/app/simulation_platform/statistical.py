"""Statistical Workbench manifest and settings contracts.

The workbench is a lane-generic configuration surface. It describes the
knobs a lane can honestly support, validates run-level settings against
that manifest, and derives deterministic hashes/lineage ids from the
validated settings. It does not run simulations or fabricate analysis.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RAWFISH_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LANES_ROOT = RAWFISH_ROOT / "frontend" / "src" / "lanes"


class StatisticalConfigError(ValueError):
    """Raised when workbench manifests or settings fail validation."""


PROVENANCE_VALUES = {
    "user-set",
    "manifest-default",
    "inferred-from-prior",
    "sampled",
    "inferred-from-parseScenario",
}

LEGACY_PROVENANCE_ALIASES = {
    "operator": "user-set",
    "registry": "manifest-default",
    "platform_taxonomy": "manifest-default",
    "lane_manifest": "manifest-default",
    "preflight_meter": "manifest-default",
}

# Rich Workbench manifests annotate impact hints with
# source: "runSurrogateExplainer" so the cockpit does not mistake static
# kernel hints for executed findings.
IMPACT_HINT_SOURCE = "runSurrogateExplainer"


def canonical_field_id(field_id: str) -> str:
    """Return RawFish's canonical dotted Statistical Workbench field id."""

    return field_id


def _strict_config() -> ConfigDict:
    return ConfigDict(populate_by_name=True, extra="forbid")


class StatisticalOption(BaseModel):
    """One selectable value for choice-like workbench controls."""

    model_config = _strict_config()

    value: Any
    label: str = Field(..., min_length=1)


class StatisticalWorkbenchField(BaseModel):
    """One Statistical Workbench control exposed by a lane.

    The RawFish contract uses dotted ids (``run.run_seed``,
    ``cohort.activity_level``). Cockpit slot names may still describe
    Workbench panels, but settings fields are always canonical ids.
    """

    model_config = _strict_config()

    field_id: str = Field(..., alias="id", min_length=1)
    label: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    control: str = Field(..., min_length=1)
    default: Any
    description: str = Field(..., min_length=1)
    why_it_matters: Optional[str] = None
    citation_key: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    units: Optional[str] = None
    modalities: List[str] = Field(default_factory=list)
    validation: List[Dict[str, Any]] = Field(default_factory=list)
    lane_applicability: List[str] = Field(default_factory=list)
    affects: List[str] = Field(default_factory=list)
    options: List[StatisticalOption] = Field(default_factory=list)
    impact_hint: Optional[Any] = None
    impact_model: Optional[Dict[str, Any]] = None
    citation_policy: Optional[Dict[str, Any]] = None
    provenance: List[str] = Field(default_factory=list)
    provenance_policy: Optional[Dict[str, Any]] = None
    lock_supported: bool = True
    applies_to: List[str] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)

    @field_validator("field_id")
    @classmethod
    def _field_id_is_canonical(cls, value: str) -> str:
        if "." not in value:
            raise ValueError(
                "statistical manifest field ids must be dotted canonical ids"
            )
        if "/" in value or value.startswith(".") or value.endswith("."):
            raise ValueError("statistical manifest field id is not canonical")
        return value

    @model_validator(mode="after")
    def _field_contract_is_complete(self) -> "StatisticalWorkbenchField":
        if self.kind in {"choice", "multi_select"} and not self.options:
            raise ValueError(f"{self.field_id} must declare options")
        if "." in self.field_id:
            if not (self.why_it_matters or "").strip():
                raise ValueError(f"{self.field_id} must declare why_it_matters")
            if not (self.citation_key or "").strip():
                raise ValueError(f"{self.field_id} must declare citation_key")
            if not self.modalities:
                raise ValueError(f"{self.field_id} must declare modalities")
            if not self.lane_applicability:
                raise ValueError(f"{self.field_id} must declare lane_applicability")
            if not self.affects:
                raise ValueError(f"{self.field_id} must declare affects")
            if not isinstance(self.impact_hint, dict):
                raise ValueError(f"{self.field_id} must declare structured impact_hint")
            if self.impact_hint.get("source") != "runSurrogateExplainer":
                raise ValueError(
                    f"{self.field_id} impact_hint.source must be runSurrogateExplainer"
                )
        return self


class StatisticalManifest(BaseModel):
    """Lane-owned Statistical Workbench manifest."""

    model_config = _strict_config()

    lane_id: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    fields: List[StatisticalWorkbenchField] = Field(..., min_length=1)
    impact_model: Optional[Dict[str, Any]] = None
    citation_policy: Optional[Dict[str, Any]] = None
    provenance: List[str] = Field(default_factory=list)
    provenance_policy: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _field_ids_are_unique(self) -> "StatisticalManifest":
        ids = [field.field_id for field in self.fields]
        duplicates = sorted({field_id for field_id in ids if ids.count(field_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate statistical field ids: {duplicates!r}")
        return self

    @property
    def field_ids(self) -> List[str]:
        return [canonical_field_id(field.field_id) for field in self.fields]

    def model_dump_public(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class StatisticalConfigPayload(BaseModel):
    """Validated run-level Statistical Workbench settings payload."""

    model_config = _strict_config()

    lane_id: str
    version: str
    field_ids: List[str]
    settings: Dict[str, Any]
    statistical_settings_hash: str
    persona_salts_resolved: Dict[str, str] = Field(default_factory=dict)
    prior_attachments: Dict[str, Any] = Field(default_factory=dict)
    provenance_per_field: Dict[str, str] = Field(default_factory=dict)
    lock_state_per_field: Dict[str, bool] = Field(default_factory=dict)


class StatisticalRuntimeOverrides(BaseModel):
    """Known settings that currently feed real runtime seams."""

    model_config = _strict_config()

    ensemble_n: Optional[int] = None
    run_seed: Optional[int] = None
    drift_horizon_rounds: Optional[int] = None
    rounds: Optional[int] = None
    seed: Optional[int] = None
    persona_salts: Optional[Dict[str, str]] = None
    active_hours: Optional[List[int]] = None
    active_hours_by_cohort: Optional[Dict[str, List[int]]] = None
    cohort_overrides: Optional[Dict[str, Dict[str, Any]]] = None


class StatisticalRuntimeContext(BaseModel):
    """Persisted statistical settings context for lane artifacts/runners."""

    model_config = _strict_config()

    lane_id: str
    version: str
    statistical_settings_hash: str
    settings: Dict[str, Any]
    runtime_overrides: StatisticalRuntimeOverrides
    persona_salts_resolved: Dict[str, str] = Field(default_factory=dict)
    prior_attachments: Dict[str, Any] = Field(default_factory=dict)
    provenance_per_field: Dict[str, str] = Field(default_factory=dict)
    lock_state_per_field: Dict[str, bool] = Field(default_factory=dict)


class StatisticalConfigSnapshot(BaseModel):
    """Normalized public workbench snapshot persisted with a run."""

    model_config = _strict_config()

    lane_id: str
    version: str
    field_ids: List[str]
    settings: Dict[str, Any]
    statistical_settings_hash: str
    run_overrides: StatisticalRuntimeOverrides
    persona_salts_resolved: Dict[str, str] = Field(default_factory=dict)
    prior_attachments: Dict[str, Any] = Field(default_factory=dict)
    provenance_per_field: Dict[str, str] = Field(default_factory=dict)
    lock_state_per_field: Dict[str, bool] = Field(default_factory=dict)


class StatisticalLineageEvent(BaseModel):
    """One deterministic lineage event for a settings reseed/variant."""

    model_config = _strict_config()

    variant_id: str
    operation: str
    parent_statistical_settings_hash: str
    statistical_settings_hash: str
    seed: Optional[int] = None
    reason: str
    patch: Dict[str, Any] = Field(default_factory=dict)
    applied_ops: List[Dict[str, Any]] = Field(default_factory=list)
    parent_settings: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any]
    created_at: str


_WORKBENCH_RUN_PATH_TO_FIELD = {
    "/run/run_seed": "run.run_seed",
    "/run/ensemble_n": "run.ensemble_n",
    "/run/drift_horizon_rounds": "run.drift_horizon_rounds",
    "/run/ensemble_seeds": "run.ensemble_seeds",
    "/run/t_structure": "run.t_structure",
    "/run/t_narrative": "run.t_narrative",
    "/run/assumption_confirmation_ratio": "run.assumption_confirmation_ratio",
}

_WORKBENCH_COHORT_STATISTICAL_TO_FIELD = {
    "active_hours": "cohort.active_hours",
    "activity_level": "cohort.activity_level",
    "posts_per_hour": "cohort.posts_per_hour",
    "comments_per_hour": "cohort.comments_per_hour",
    "sentiment_bias": "cohort.sentiment_bias",
    "influence_weight": "cohort.influence_weight",
}

_WORKBENCH_COHORT_SETTING_ID = "cohorts"
_WORKBENCH_COHORT_MAP_PREFIX = "cohort-map:"
_WORKBENCH_COHORT_MAP_SEPARATOR = "\x1f"


def _cohort_map_key(
    *,
    cohort_id: str,
    section: str,
    name: str,
    field_id: str,
) -> str:
    return _WORKBENCH_COHORT_MAP_PREFIX + _WORKBENCH_COHORT_MAP_SEPARATOR.join(
        [cohort_id, section, name, canonical_field_id(field_id)]
    )


def _parse_cohort_map_key(map_key: str) -> Optional[Tuple[str, str, str, str]]:
    if not map_key.startswith(_WORKBENCH_COHORT_MAP_PREFIX):
        return None
    raw = map_key[len(_WORKBENCH_COHORT_MAP_PREFIX):]
    parts = raw.split(_WORKBENCH_COHORT_MAP_SEPARATOR)
    if len(parts) != 4:
        return None
    cohort_id, section, name, field_id = parts
    if not cohort_id or not section or not name or not field_id:
        return None
    return cohort_id, section, name, field_id


def load_statistical_manifest(
    lane_id: str,
    *,
    lanes_root: Path = DEFAULT_LANES_ROOT,
    manifest_filename: str = "statistical.manifest.json",
) -> StatisticalManifest:
    """Load and validate a lane Statistical Workbench manifest."""

    if not lane_id or "/" in lane_id or ".." in lane_id:
        raise StatisticalConfigError(f"unsafe lane_id for statistical manifest: {lane_id!r}")
    filename = Path(manifest_filename).name
    path = lanes_root / lane_id / filename
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StatisticalConfigError(
            f"statistical manifest not found for lane {lane_id!r}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise StatisticalConfigError(
            f"statistical manifest JSON invalid for lane {lane_id!r}"
        ) from exc

    try:
        manifest = StatisticalManifest.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - pydantic keeps details on the cause.
        raise StatisticalConfigError(
            f"statistical manifest invalid for lane {lane_id!r}: {exc}"
        ) from exc
    if manifest.lane_id != lane_id:
        raise StatisticalConfigError(
            f"statistical manifest lane_id={manifest.lane_id!r} "
            f"does not match requested lane {lane_id!r}"
        )
    _canonicalize_manifest_fields(manifest)
    return manifest


def _canonicalize_manifest_fields(manifest: StatisticalManifest) -> None:
    """Normalize manifest field ids without accepting alternate setting names."""

    for field in manifest.fields:
        field.field_id = canonical_field_id(field.field_id)
        field.aliases = sorted(
            alias
            for alias in set(field.aliases or [])
            if alias and alias != field.field_id
        )


def _option_values(field: StatisticalWorkbenchField) -> set[Any]:
    return {option.value for option in field.options}


def _validate_value(field: StatisticalWorkbenchField, value: Any) -> Any:
    """Validate and normalize a field value without guessing."""

    if field.kind == "integer":
        if isinstance(value, bool):
            raise StatisticalConfigError(f"{field.field_id} must be an integer")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise StatisticalConfigError(f"{field.field_id} must be an integer") from exc
        if field.min is not None and normalized < field.min:
            raise StatisticalConfigError(f"{field.field_id} must be >= {field.min:g}")
        if field.max is not None and normalized > field.max:
            raise StatisticalConfigError(f"{field.field_id} must be <= {field.max:g}")
        return normalized

    if field.kind == "number":
        if isinstance(value, bool):
            raise StatisticalConfigError(f"{field.field_id} must be a number")
        try:
            normalized_float = float(value)
        except (TypeError, ValueError) as exc:
            raise StatisticalConfigError(f"{field.field_id} must be a number") from exc
        if field.min is not None and normalized_float < field.min:
            raise StatisticalConfigError(f"{field.field_id} must be >= {field.min:g}")
        if field.max is not None and normalized_float > field.max:
            raise StatisticalConfigError(f"{field.field_id} must be <= {field.max:g}")
        return normalized_float

    if field.kind == "boolean":
        if not isinstance(value, bool):
            raise StatisticalConfigError(f"{field.field_id} must be a boolean")
        return value

    if field.kind == "choice":
        if value not in _option_values(field):
            raise StatisticalConfigError(f"{field.field_id} is not an allowed choice")
        return value

    if field.kind == "multi_select":
        if not isinstance(value, list):
            raise StatisticalConfigError(f"{field.field_id} must be a list")
        allowed = _option_values(field)
        normalized_list: list[Any] = []
        for item in value:
            if item not in allowed:
                raise StatisticalConfigError(
                    f"{field.field_id} contains unsupported value {item!r}"
                )
            if item not in normalized_list:
                normalized_list.append(item)
        return normalized_list

    if field.kind == "active_hours":
        if not isinstance(value, list):
            raise StatisticalConfigError(f"{field.field_id} must be a list of hours")
        hours: list[int] = []
        for item in value:
            if isinstance(item, bool):
                raise StatisticalConfigError(f"{field.field_id} hours must be integers")
            try:
                hour = int(item)
            except (TypeError, ValueError) as exc:
                raise StatisticalConfigError(
                    f"{field.field_id} hours must be integers"
                ) from exc
            if hour < 0 or hour > 23:
                raise StatisticalConfigError(f"{field.field_id} hours must be 0..23")
            if hour not in hours:
                hours.append(hour)
        return sorted(hours)

    if field.kind == "salt_map":
        if not isinstance(value, dict):
            raise StatisticalConfigError(f"{field.field_id} must be an object")
        return {str(key): str(val) for key, val in sorted(value.items())}

    if field.kind == "text":
        if not isinstance(value, str):
            raise StatisticalConfigError(f"{field.field_id} must be text")
        return value

    raise StatisticalConfigError(f"{field.field_id} declares unsupported kind {field.kind!r}")


def _fields_by_any_id(manifest: StatisticalManifest) -> Dict[str, StatisticalWorkbenchField]:
    fields: Dict[str, StatisticalWorkbenchField] = {}
    for field in manifest.fields:
        fields[field.field_id] = field
    return fields


def _canonical_manifest_field_ids(manifest: StatisticalManifest) -> List[str]:
    return [canonical_field_id(field.field_id) for field in manifest.fields]


def _field_for_canonical_id(
    manifest: StatisticalManifest,
    field_id: str,
) -> StatisticalWorkbenchField:
    fields_by_id = _fields_by_any_id(manifest)
    field = fields_by_id.get(canonical_field_id(field_id))
    if field is None:
        raise StatisticalConfigError("path_not_in_allowlist")
    return field


def _validate_cohort_overrides(
    manifest: StatisticalManifest,
    payload: Any,
) -> Dict[str, Dict[str, Any]]:
    """Validate the nested per-cohort Workbench settings map.

    Shape:

    ``{"fans": {"statistical": {"active_hours": [...]}}}``

    Each nested setting must correspond to a field published by the lane's
    statistical manifest. That keeps one lane from accepting settings that its
    adapter cannot interpret.
    """

    if payload in (None, {}):
        return {}
    if not isinstance(payload, dict):
        raise StatisticalConfigError("cohorts must be an object")
    validated: Dict[str, Dict[str, Any]] = {}
    manifest_field_ids = set(_canonical_manifest_field_ids(manifest))
    for raw_cohort_id, raw_sections in sorted(payload.items()):
        cohort_id = str(raw_cohort_id)
        if not cohort_id or "/" in cohort_id:
            raise StatisticalConfigError("cohort id is not allowed")
        if not isinstance(raw_sections, dict):
            raise StatisticalConfigError(f"cohorts[{cohort_id!r}] must be an object")
        cohort_payload: Dict[str, Any] = {}
        for section, raw_values in sorted(raw_sections.items()):
            if not isinstance(raw_values, dict):
                raise StatisticalConfigError(
                    f"cohorts[{cohort_id!r}][{section!r}] must be an object"
                )
            section_payload: Dict[str, Any] = {}
            for name, raw_value in sorted(raw_values.items()):
                if section == "statistical":
                    field_id = _WORKBENCH_COHORT_STATISTICAL_TO_FIELD.get(str(name))
                elif section == "priors" and name == "bundle_id":
                    field_id = "cohort.priors.attached_priors_id"
                elif section == "drift" and name in {
                    "axis",
                    "policy_drift_axis",
                    "counterfactual_axis",
                    "sensitivity_metric",
                    "uncertainty_band",
                }:
                    field_id = {
                        "axis": "cohort.drift.axis",
                        "policy_drift_axis": "cohort.drift.axis",
                        "counterfactual_axis": "variant.counterfactual_axis",
                        "sensitivity_metric": "variant.sensitivity_metric",
                        "uncertainty_band": "manifest.uncertainty_band",
                    }[str(name)]
                else:
                    field_id = None
                if not field_id:
                    raise StatisticalConfigError("path_not_in_allowlist")
                field_id = canonical_field_id(field_id)
                if field_id not in manifest_field_ids:
                    raise StatisticalConfigError("path_not_in_allowlist")
                field = _field_for_canonical_id(manifest, field_id)
                section_payload[str(name)] = _validate_value(field, raw_value)
            if section_payload:
                cohort_payload[str(section)] = section_payload
        if cohort_payload:
            validated[cohort_id] = cohort_payload
    return validated


def default_statistical_settings(manifest: StatisticalManifest) -> Dict[str, Any]:
    """Return manifest defaults after the same validation as user settings."""

    return {
        canonical_field_id(field.field_id): _validate_value(field, field.default)
        for field in manifest.fields
    }


def validate_statistical_settings(
    manifest: StatisticalManifest,
    payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validate settings against a manifest and fill omitted defaults."""

    incoming = payload or {}
    if not isinstance(incoming, dict):
        raise StatisticalConfigError("statistical settings must be an object")
    fields_by_id = _fields_by_any_id(manifest)
    cohort_payload = incoming.get(_WORKBENCH_COHORT_SETTING_ID)
    normalized_incoming = {
        canonical_field_id(str(field_id)): value
        for field_id, value in incoming.items()
        if field_id != _WORKBENCH_COHORT_SETTING_ID
    }
    known_ids = set(_canonical_manifest_field_ids(manifest))
    unknown = sorted(set(normalized_incoming) - known_ids)
    if unknown:
        raise StatisticalConfigError(f"unknown statistical settings: {unknown!r}")
    settings = default_statistical_settings(manifest)
    for field_id, value in normalized_incoming.items():
        field = fields_by_id.get(field_id)
        if field is None:
            raise StatisticalConfigError(f"unknown statistical setting: {field_id!r}")
        settings[field_id] = _validate_value(field, value)
    if cohort_payload is not None:
        cohort_overrides = _validate_cohort_overrides(manifest, cohort_payload)
        if cohort_overrides:
            settings[_WORKBENCH_COHORT_SETTING_ID] = cohort_overrides
    return settings


def default_provenance_per_field(manifest: StatisticalManifest) -> Dict[str, str]:
    """Return default provenance values for every published field."""

    provenance: Dict[str, str] = {}
    for field in manifest.fields:
        raw_initial: Optional[str] = None
        if isinstance(field.provenance_policy, dict):
            raw_initial = field.provenance_policy.get("initial")
        if not raw_initial and field.provenance:
            raw_initial = field.provenance[0]
        initial = LEGACY_PROVENANCE_ALIASES.get(
            str(raw_initial or "manifest-default"),
            str(raw_initial or "manifest-default"),
        )
        if initial not in PROVENANCE_VALUES:
            initial = "manifest-default"
        provenance[canonical_field_id(field.field_id)] = initial
    return provenance


def provenance_per_field_for_settings(
    manifest: StatisticalManifest,
    settings: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    """Return provenance, marking explicit non-default field values as user-set."""

    provenance = default_provenance_per_field(manifest)
    if not isinstance(settings, dict):
        return provenance
    defaults = default_statistical_settings(manifest)
    known_ids = set(_canonical_manifest_field_ids(manifest))
    for raw_field_id, raw_value in settings.items():
        field_id = canonical_field_id(str(raw_field_id))
        if field_id == _WORKBENCH_COHORT_SETTING_ID:
            continue
        if field_id not in known_ids:
            continue
        if raw_value != defaults.get(field_id):
            provenance[field_id] = "user-set"
    return provenance


def default_lock_state_per_field(manifest: StatisticalManifest) -> Dict[str, bool]:
    """Return default lock values for every published field."""

    locks: Dict[str, bool] = {}
    for field in manifest.fields:
        lock = False
        if isinstance(field.provenance_policy, dict):
            lock = bool(field.provenance_policy.get("lock_default", False))
        locks[canonical_field_id(field.field_id)] = lock
    return locks


def persona_salts_from_settings(settings: Dict[str, Any]) -> Dict[str, str]:
    """Extract resolved per-cohort salts from canonical settings."""

    raw = settings.get("run.persona_salts")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in sorted(raw.items())}


def prior_attachments_from_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Extract attached prior metadata without fabricating absent priors."""

    attached_id = settings.get("cohort.priors.attached_priors_id")
    if attached_id in (None, ""):
        return {}
    return {"attached_priors_id": attached_id}


def cohort_overrides_from_settings(
    settings: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Extract validated per-cohort Workbench settings without flattening cohort ids."""

    raw = settings.get(_WORKBENCH_COHORT_SETTING_ID)
    if not isinstance(raw, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for cohort_id, sections in sorted(raw.items()):
        if not isinstance(sections, dict):
            continue
        cohort_payload: Dict[str, Any] = {}
        for section, values in sorted(sections.items()):
            if isinstance(values, dict):
                cohort_payload[str(section)] = {
                    str(key): value for key, value in sorted(values.items())
                }
        if cohort_payload:
            normalized[str(cohort_id)] = cohort_payload
    return normalized


def _normalize_active_hours(value: Any) -> Optional[List[int]]:
    if not isinstance(value, list):
        return None
    hours: list[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            hour = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)
    return sorted(hours) if hours else None


def active_hours_by_cohort_from_settings(
    settings: Dict[str, Any],
) -> Dict[str, List[int]]:
    """Extract per-cohort active-hours overrides from the cohort settings map."""

    by_cohort: Dict[str, List[int]] = {}
    for cohort_id, sections in cohort_overrides_from_settings(settings).items():
        statistical = sections.get("statistical")
        if not isinstance(statistical, dict):
            continue
        hours = _normalize_active_hours(statistical.get("active_hours"))
        if hours:
            by_cohort[cohort_id] = hours
    return by_cohort


def statistical_settings_hash(
    *,
    lane_id: str,
    version: str,
    settings: Dict[str, Any],
    lock_state_per_field: Optional[Dict[str, bool]] = None,
    prior_attachments: Optional[Dict[str, Any]] = None,
    persona_salts: Optional[Dict[str, str]] = None,
) -> str:
    """Derive the stable hash for validated statistical settings."""

    canonical = json.dumps(
        {
            "lane_id": lane_id,
            "version": version,
            "settings": settings,
            "lock_state_per_field": lock_state_per_field or {},
            "prior_attachments": prior_attachments or {},
            "persona_salts": persona_salts or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_statistical_config_payload(
    manifest: StatisticalManifest,
    settings: Optional[Dict[str, Any]] = None,
) -> StatisticalConfigPayload:
    """Build the public config payload for a manifest/settings pair."""

    validated = validate_statistical_settings(manifest, settings)
    locks = default_lock_state_per_field(manifest)
    runtime = runtime_overrides_from_settings(validated)
    persona_salts = runtime.persona_salts or persona_salts_from_settings(validated)
    prior_attachments = prior_attachments_from_settings(validated)
    provenance = provenance_per_field_for_settings(manifest, settings)
    return StatisticalConfigPayload(
        lane_id=manifest.lane_id,
        version=manifest.version,
        field_ids=_canonical_manifest_field_ids(manifest),
        settings=validated,
        persona_salts_resolved=persona_salts,
        prior_attachments=prior_attachments,
        provenance_per_field=provenance,
        lock_state_per_field=locks,
        statistical_settings_hash=statistical_settings_hash(
            lane_id=manifest.lane_id,
            version=manifest.version,
            settings=validated,
            lock_state_per_field=locks,
            prior_attachments=prior_attachments,
            persona_salts=persona_salts,
        ),
    )


def build_statistical_config_snapshot(
    manifest: StatisticalManifest,
    settings: Optional[Dict[str, Any]] = None,
) -> StatisticalConfigSnapshot:
    """Validate settings and expose the public, load-bearing run snapshot."""

    config = build_statistical_config_payload(manifest, settings)
    return StatisticalConfigSnapshot(
        lane_id=config.lane_id,
        version=config.version,
        field_ids=config.field_ids,
        settings=config.settings,
        statistical_settings_hash=config.statistical_settings_hash,
        run_overrides=runtime_overrides_from_settings(config.settings),
        persona_salts_resolved=config.persona_salts_resolved,
        prior_attachments=config.prior_attachments,
        provenance_per_field=config.provenance_per_field,
        lock_state_per_field=config.lock_state_per_field,
    )


def runtime_overrides_from_settings(
    settings: Optional[Dict[str, Any]],
) -> StatisticalRuntimeOverrides:
    """Extract only settings that have real runtime consumers today."""

    if not isinstance(settings, dict):
        return StatisticalRuntimeOverrides()

    def _slot(*field_ids: str) -> Any:
        for field_id in field_ids:
            if field_id in settings:
                return settings.get(field_id)
        return None

    def _int_slot(*field_ids: str, min_value: int = 1) -> Optional[int]:
        value = _slot(*field_ids)
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= min_value else None

    active_hours_raw = _slot("cohort.active_hours")
    active_hours = _normalize_active_hours(active_hours_raw)

    persona_salts_raw = _slot("run.persona_salts")
    persona_salts: Optional[Dict[str, str]] = None
    if isinstance(persona_salts_raw, dict):
        persona_salts = {
            str(key): str(value)
            for key, value in sorted(persona_salts_raw.items())
        }
    cohort_overrides = cohort_overrides_from_settings(settings)
    active_hours_by_cohort = active_hours_by_cohort_from_settings(settings)

    run_seed = _int_slot("run.run_seed", min_value=0)
    rounds = _int_slot(
        "run.drift_horizon_rounds",
        "run.rounds",
    )

    return StatisticalRuntimeOverrides(
        ensemble_n=_int_slot("run.ensemble_n"),
        run_seed=run_seed,
        drift_horizon_rounds=rounds,
        rounds=rounds,
        seed=run_seed,
        persona_salts=persona_salts,
        active_hours=active_hours,
        active_hours_by_cohort=(
            active_hours_by_cohort if active_hours_by_cohort else None
        ),
        cohort_overrides=cohort_overrides if cohort_overrides else None,
    )


def build_statistical_runtime_context(
    manifest: StatisticalManifest,
    settings: Optional[Dict[str, Any]] = None,
) -> StatisticalRuntimeContext:
    """Build the run artifact that makes workbench settings auditable."""

    snapshot = build_statistical_config_snapshot(manifest, settings)
    return StatisticalRuntimeContext(
        lane_id=snapshot.lane_id,
        version=snapshot.version,
        statistical_settings_hash=snapshot.statistical_settings_hash,
        settings=snapshot.settings,
        runtime_overrides=snapshot.run_overrides,
        persona_salts_resolved=snapshot.persona_salts_resolved,
        prior_attachments=snapshot.prior_attachments,
        provenance_per_field=snapshot.provenance_per_field,
        lock_state_per_field=snapshot.lock_state_per_field,
    )


def build_statistical_runtime_context_from_config(
    config: StatisticalConfigPayload,
) -> StatisticalRuntimeContext:
    """Build the runtime context from an already-validated config."""

    return StatisticalRuntimeContext(
        lane_id=config.lane_id,
        version=config.version,
        statistical_settings_hash=config.statistical_settings_hash,
        settings=config.settings,
        runtime_overrides=runtime_overrides_from_settings(config.settings),
        persona_salts_resolved=config.persona_salts_resolved,
        prior_attachments=config.prior_attachments,
        provenance_per_field=config.provenance_per_field,
        lock_state_per_field=config.lock_state_per_field,
    )


def apply_statistical_settings_patch(
    manifest: StatisticalManifest,
    current_settings: Dict[str, Any],
    patch: Dict[str, Any],
) -> StatisticalConfigPayload:
    """Apply a partial settings patch and return the normalized config."""

    if not isinstance(patch, dict) or not patch:
        raise StatisticalConfigError("statistical settings patch must be a non-empty object")
    parent = build_statistical_config_payload(manifest, current_settings)
    next_settings = dict(parent.settings)
    next_settings.update({
        canonical_field_id(str(field_id)): value
        for field_id, value in patch.items()
    })
    return build_statistical_config_payload(manifest, next_settings)


def _json_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _pointer_tokens(path: Any) -> List[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise StatisticalConfigError("path_not_in_allowlist")
    return [_json_pointer_token(token) for token in path.split("/")[1:]]


def _field_default(manifest: StatisticalManifest, field_id: str) -> Any:
    field_id = canonical_field_id(field_id)
    for field in manifest.fields:
        if canonical_field_id(field.field_id) == field_id:
            return _validate_value(field, field.default)
    raise StatisticalConfigError("path_not_in_allowlist")


def _field_for_workbench_path(
    manifest: StatisticalManifest,
    path: str,
) -> Tuple[str, Optional[str]]:
    """Map Workbench JSON Pointer paths to canonical settings fields.

    The cockpit sends operator-friendly pointers such as ``/run/run_seed`` and
    ``/run/persona_salts/<cohort_id>``. Internally, those pointers resolve to
    persisted setting keys like ``run.run_seed`` and ``cohort.active_hours``.
    The manifest is the allowlist: if the lane does not publish a setting, the
    patch is rejected before it can reach prepare/start.
    """

    if path in _WORKBENCH_RUN_PATH_TO_FIELD:
        field_id = _WORKBENCH_RUN_PATH_TO_FIELD[path]
        field_id = canonical_field_id(field_id)
        if field_id not in _canonical_manifest_field_ids(manifest):
            raise StatisticalConfigError("path_not_in_allowlist")
        return field_id, None

    tokens = _pointer_tokens(path)
    if tokens[:2] == ["run", "persona_salts"]:
        field_id = "run.persona_salts"
        if field_id not in _canonical_manifest_field_ids(manifest):
            raise StatisticalConfigError("path_not_in_allowlist")
        if len(tokens) == 2:
            return field_id, None
        if len(tokens) == 3 and tokens[2]:
            return field_id, tokens[2]
        raise StatisticalConfigError("path_not_in_allowlist")

    if len(tokens) == 4 and tokens[0] == "cohorts" and tokens[1]:
        cohort_id = tokens[1]
        section = tokens[2]
        name = tokens[3]
        if section == "statistical":
            field_id = _WORKBENCH_COHORT_STATISTICAL_TO_FIELD.get(name)
        elif section == "priors" and name == "bundle_id":
            field_id = "cohort.priors.attached_priors_id"
        elif section == "drift" and name in {
            "axis",
            "policy_drift_axis",
            "counterfactual_axis",
            "sensitivity_metric",
            "uncertainty_band",
        }:
            field_id = {
                "axis": "cohort.drift.axis",
                "policy_drift_axis": "cohort.drift.axis",
                "counterfactual_axis": "variant.counterfactual_axis",
                "sensitivity_metric": "variant.sensitivity_metric",
                "uncertainty_band": "manifest.uncertainty_band",
            }[name]
        else:
            field_id = None
        if field_id:
            field_id = canonical_field_id(field_id)
        if field_id and field_id in _canonical_manifest_field_ids(manifest):
            return _WORKBENCH_COHORT_SETTING_ID, _cohort_map_key(
                cohort_id=cohort_id,
                section=section,
                name=name,
                field_id=field_id,
            )
    raise StatisticalConfigError("path_not_in_allowlist")


def apply_statistical_json_patch(
    manifest: StatisticalManifest,
    current_settings: Dict[str, Any],
    ops: Sequence[Dict[str, Any]],
) -> Tuple[StatisticalConfigPayload, List[Dict[str, Any]]]:
    """Apply RFC 6902-style Workbench ops to persisted settings.

    Supports the RawFish Workbench subset of RFC6902: ``add``, ``replace``,
    and ``remove``. All paths are allowlisted through
    :func:`_field_for_workbench_path`; any unsupported path aborts the whole patch
    by raising :class:`StatisticalConfigError` with ``path_not_in_allowlist``.
    """

    if not isinstance(ops, Sequence) or isinstance(ops, (str, bytes)) or not ops:
        raise StatisticalConfigError("statistical patch ops must be a non-empty list")

    parent = build_statistical_config_payload(manifest, current_settings)
    next_settings: Dict[str, Any] = dict(parent.settings)
    applied_ops: List[Dict[str, Any]] = []

    for index, raw in enumerate(ops):
        if not isinstance(raw, dict):
            raise StatisticalConfigError(f"op #{index} must be an object")
        op = raw.get("op")
        if op not in {"add", "replace", "remove"}:
            raise StatisticalConfigError(f"op #{index} has unsupported op {op!r}")
        path = raw.get("path")
        field_id, map_key = _field_for_workbench_path(manifest, str(path or ""))
        if op in {"add", "replace"} and "value" not in raw:
            raise StatisticalConfigError(f"op #{index} requires value")
        if op == "remove" and "value" in raw:
            raise StatisticalConfigError(f"op #{index} remove must not include value")

        cohort_map = _parse_cohort_map_key(map_key) if map_key is not None else None

        if cohort_map is not None:
            cohort_id, section, name, base_field_id = cohort_map
            current_cohorts = next_settings.get(_WORKBENCH_COHORT_SETTING_ID)
            if not isinstance(current_cohorts, dict):
                current_cohorts = {}
            updated_cohorts = json.loads(json.dumps(current_cohorts))
            if op == "remove":
                section_values = (
                    updated_cohorts
                    .get(cohort_id, {})
                    .get(section, {})
                )
                if isinstance(section_values, dict):
                    section_values.pop(name, None)
                    if not section_values:
                        updated_cohorts.get(cohort_id, {}).pop(section, None)
                    if not updated_cohorts.get(cohort_id):
                        updated_cohorts.pop(cohort_id, None)
            else:
                field = _field_for_canonical_id(manifest, base_field_id)
                updated_cohorts.setdefault(cohort_id, {})
                updated_cohorts[cohort_id].setdefault(section, {})
                updated_cohorts[cohort_id][section][name] = _validate_value(
                    field,
                    raw["value"],
                )
            if updated_cohorts:
                next_settings[_WORKBENCH_COHORT_SETTING_ID] = updated_cohorts
            else:
                next_settings.pop(_WORKBENCH_COHORT_SETTING_ID, None)
        elif map_key is None:
            if op == "remove":
                next_settings[field_id] = _field_default(manifest, field_id)
            else:
                next_settings[field_id] = raw["value"]
        else:
            current_map = next_settings.get(field_id)
            if not isinstance(current_map, dict):
                current_map = {}
            updated_map = {str(key): str(value) for key, value in current_map.items()}
            if op == "remove":
                updated_map.pop(map_key, None)
            else:
                updated_map[map_key] = str(raw["value"])
            next_settings[field_id] = updated_map

        applied_ops.append({
            "op": op,
            "path": path,
            "field_id": (
                cohort_map[3] if cohort_map is not None else field_id
            ),
            **(
                {
                    "cohort_id": cohort_map[0],
                    "section": cohort_map[1],
                    "name": cohort_map[2],
                }
                if cohort_map is not None
                else ({"map_key": map_key} if map_key is not None else {})
            ),
        })

    return build_statistical_config_payload(manifest, next_settings), applied_ops


def statistical_settings_diff_ops(
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return a small RFC6902-style diff for flat settings dictionaries."""

    left = before if isinstance(before, dict) else {}
    right = after if isinstance(after, dict) else {}
    ops: List[Dict[str, Any]] = []
    for key in sorted(set(left) | set(right)):
        path = f"/settings/{key}"
        if key not in right:
            ops.append({"op": "remove", "path": path})
        elif key not in left:
            ops.append({"op": "add", "path": path, "value": right[key]})
        elif left[key] != right[key]:
            ops.append({"op": "replace", "path": path, "value": right[key]})
    return ops


def build_reseed_lineage_event(
    *,
    manifest: StatisticalManifest,
    current_settings: Dict[str, Any],
    seed: Optional[int] = None,
    patch: Optional[Dict[str, Any]] = None,
    reason: str,
    created_at: str,
) -> StatisticalLineageEvent:
    """Return a deterministic lineage event for a settings variant.

    ``seed`` preserves the original seed-only reseed API. ``patch`` allows
    Workbench settings deltas over the same validated canonical fields.
    """

    patch_payload: Dict[str, Any] = {
        canonical_field_id(str(field_id)): value
        for field_id, value in dict(patch or {}).items()
    }
    manifest_field_ids = set(_canonical_manifest_field_ids(manifest))
    if seed is not None and "run.run_seed" not in manifest_field_ids:
        raise StatisticalConfigError(
            f"lane {manifest.lane_id!r} does not expose run.run_seed"
        )
    seed_int: Optional[int] = None
    if seed is not None:
        if isinstance(seed, bool):
            raise StatisticalConfigError("seed must be an integer")
        seed_int = int(seed)
        patch_payload["run.run_seed"] = seed_int
    if not patch_payload:
        raise StatisticalConfigError("statistical settings patch must be a non-empty object")

    parent = build_statistical_config_payload(manifest, current_settings)
    child = apply_statistical_settings_patch(manifest, parent.settings, patch_payload)
    resolved_patch = {
        field_id: child.settings[field_id]
        for field_id in sorted(patch_payload)
    }
    operation = (
        "reseed"
        if set(resolved_patch) == {"run.run_seed"}
        else "settings_patch"
    )
    if seed_int is None:
        child_seed = child.settings.get("run.run_seed")
        seed_int = child_seed if isinstance(child_seed, int) else None
    variant_material = json.dumps(
        {
            "lane_id": manifest.lane_id,
            "operation": operation,
            "parent": parent.statistical_settings_hash,
            "child": child.statistical_settings_hash,
            "patch": resolved_patch,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return StatisticalLineageEvent(
        variant_id="stat_" + hashlib.sha256(
            variant_material.encode("utf-8")
        ).hexdigest()[:12],
        operation=operation,
        parent_statistical_settings_hash=parent.statistical_settings_hash,
        statistical_settings_hash=child.statistical_settings_hash,
        seed=seed_int,
        reason=reason or "operator_reseed",
        patch=resolved_patch,
        parent_settings=parent.settings,
        settings=child.settings,
        created_at=created_at,
    )


__all__ = [
    "StatisticalConfigError",
    "StatisticalConfigPayload",
    "StatisticalConfigSnapshot",
    "StatisticalLineageEvent",
    "StatisticalManifest",
    "StatisticalRuntimeContext",
    "StatisticalRuntimeOverrides",
    "StatisticalWorkbenchField",
    "apply_statistical_json_patch",
    "apply_statistical_settings_patch",
    "build_reseed_lineage_event",
    "build_statistical_config_snapshot",
    "build_statistical_config_payload",
    "build_statistical_runtime_context",
    "build_statistical_runtime_context_from_config",
    "canonical_field_id",
    "default_lock_state_per_field",
    "default_provenance_per_field",
    "default_statistical_settings",
    "load_statistical_manifest",
    "provenance_per_field_for_settings",
    "runtime_overrides_from_settings",
    "statistical_settings_diff_ops",
    "statistical_settings_hash",
    "validate_statistical_settings",
]
