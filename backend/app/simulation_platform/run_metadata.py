"""Typed run metadata Pydantic model.

Persists the operational metadata of a single RawFish run to
``run_metadata.json`` next to ``state.json``. Lane packages own their
authored scenario shapes; this module owns the lane-neutral metadata
that the platform, report generation, and LLM audit sink share.

The strict typed shape constructed and persisted when a run starts.
Required fields:

* ``scenario_hash`` / ``seed_hash`` -- deterministic input fingerprints.
* ``artifact_hashes`` -- map of artifact-name -> sha256 (e.g.
  ``policy_report.json`` -> ``"abc123..."``).
* ``zep_mode`` -- ``Literal['mock','graphiti','experimental_cloud']``.
* ``scale_caps`` -- nested :class:`ScaleCaps` model with the resolved
  ``ensemble_n``, ``stakeholders``, ``rounds``, and the effective
  ``cap_value`` (POC vs LARGE).
* ``output_paths`` -- map of artifact-name -> relative path inside the
  run directory.
* ``runner_task_id`` -- optional foreground/background runner handle.

Plus the ``run_metadata`` schema fields:

* ``created_at`` (datetime).
* ``persona`` (lane family string such as ``creator`` or ``policy``).
* ``scenario_preset_id`` -- optional preset id the run was derived from.
* ``llm_audit`` -- optional list of typed :class:`LLMAuditEntry`
  per-call records (call_id, model, mode, tokens_in/out, latency_ms,
  phase, timestamp). ``None`` distinguishes "audit not collected for
  this run" from "no calls happened" (which would be ``[]``).
* ``cost_summary`` -- optional aggregated :class:`CostSummary`
  populated at run-completion time as a fast read-side index without
  having to re-walk ``llm_audit`` from scratch.
* ``errors`` / ``warnings`` -- post-run aggregation lists, appended to
  during the run; empty by default.

A helper :func:`refresh_output_path` exists so any callsite that
persists a new artifact (report, sensitivity, counterfactual variant)
can atomically refresh BOTH ``output_paths[name]`` AND
``artifact_hashes[name]`` in-place. The helper is the canonical path:
direct mutation of ``run_metadata.json`` from a route handler is a
contract violation (the helper handles missing-file skip, corrupt-file
raise, and atomic-write semantics uniformly).

``model_config = ConfigDict(extra='forbid')`` so unknown keys round-trip
through ``model_validate_json`` will hard-fail rather than silently drop
fields. ``from_policy_dict`` is the transitional constructor that
accepts the historical inline-dict shape so the existing call site can
migrate without rewriting its arg plumbing in the same gate.
"""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, Iterator, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    import fcntl as _fcntl  # type: ignore[import]
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - exercised on Windows / sandboxed envs
    _fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False


# Filename of the per-run-dir RMW lock used by the ``refresh_*`` helpers
# below. Dotfile so directory-listing helpers do not pick it up as an
# artifact. ``atomic_write_text`` already makes the *write* atomic; what
# this lock provides is serialisation of the read-modify-write window,
# without which two concurrent appenders (e.g. two background threads
# emitting LLM audit entries) can read the same baseline, mutate
# independently, and both write back, losing one entry.
_RMW_LOCK_FILENAME = ".run_metadata.lock"


@contextmanager
def _run_metadata_rmw_lock(run_dir_path: "os.PathLike[str] | str") -> Iterator[None]:
    """Acquire an exclusive lock for read-modify-write on ``run_metadata.json``.

    Blocking acquire (unlike the per-simulation start lock which is
    non-blocking and surfaces ``StartLockBusy`` to the caller). The
    contention window here is microseconds and the natural caller
    semantic is "wait my turn, then append". The lock file lives in
    ``run_dir_path`` so it is wiped together with the artifacts it
    serialises on cleanup.

    Falls back to ``O_CREAT | O_EXCL`` claim-file semantics with a
    short retry loop when fcntl is unavailable (Windows; sandboxed
    envs). The claim file is unlinked on release in that mode.
    """
    import time
    from pathlib import Path

    run_dir = Path(os.fspath(run_dir_path))
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # If the run_dir cannot be created we cannot lock; let the
        # subsequent meta_path.exists() check return False naturally.
        yield
        return

    lock_path = run_dir / _RMW_LOCK_FILENAME

    if _HAS_FCNTL:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            assert _fcntl is not None
            _fcntl.flock(fd, _fcntl.LOCK_EX)
            try:
                yield
            finally:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        return

    # O_EXCL fallback: spin-wait briefly because callers are short and
    # the lock file is unlinked on release. The cap keeps a stuck claim
    # from blocking the caller forever in pathological cases; on
    # timeout we proceed without the lock (matches the pre-lock
    # behavior of these helpers, so the failure mode is no worse than
    # before).
    deadline = time.monotonic() + 5.0
    fd: Optional[int] = None
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            time.sleep(0.01)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                time.sleep(0.01)
                continue
            raise
    if fd is None:
        # Fall through without the lock: the helpers retain
        # atomic_write_text's atomic-rename guarantee; only the RMW
        # interleave window is unprotected. Matches the pre-lock
        # behavior, so the failure mode is no worse than before.
        yield
        return
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(str(lock_path))
        except OSError:
            pass

# The import targets the leaf module directly (not the package root)
# because ``app.simulation_platform.llm.__init__`` pulls in
# ``audit_sink.py`` which calls back into THIS module for
# :func:`refresh_audit_entry`. Going through the package would trip a
# circular-import error at boot; the leaf module is dependency-free on
# the policy package.
from app.simulation_platform.llm.audit_models import LLMAuditEntry

class RunPersona(str, Enum):
    """Lane family persisted in ``run_metadata.json``.

    This intentionally lives in the platform layer so generic run
    metadata does not import a policy scenario enum just to store the
    family label. Policy lanes still pass their own enum values; Pydantic
    normalizes compatible string enums by value.
    """

    CREATOR = "creator"
    POLICY = "policy"
    VIDEO = "video"
    PRODUCT = "product"


# Compatibility alias for older lane code that imported ``Persona`` from
# ``app.policy.run_metadata``. New platform code should prefer
# ``RunPersona`` so the enum name does not imply a policy dependency.
Persona = RunPersona


# Public enum re-spelling: the spec docs refer to ``ZepMode`` symbolically
# but the wire/env contract is the three string values below. Pydantic
# enforces this at validation time.
ZepMode = Literal["mock", "graphiti", "experimental_cloud"]


class ScaleCaps(BaseModel):
    """Resolved scale-cap snapshot for the run.

    Captures the three multiplicands (``ensemble_n * stakeholders *
    rounds``) and the cap that was actually in effect when the run
    started (``SCALE_CAP_POC`` or ``SCALE_CAP_LARGE``). Stored verbatim
    so post-hoc analysis can reconstruct *why* a given run was admitted
    even after the env / config changes.
    """

    model_config = ConfigDict(extra="forbid")

    ensemble_n: Annotated[int, Field(ge=1)]
    stakeholders: Annotated[int, Field(ge=1)]
    rounds: Annotated[int, Field(ge=1)]
    cap_value: Annotated[int, Field(ge=1)]


class CostSummary(BaseModel):
    """Aggregated cost/latency/error counters for a completed run.

    Populated at run-completion time as a read-side cache so a consumer
    can show "this run cost N tokens" without walking ``llm_audit``
    from scratch. ``llm_audit`` remains the canonical source of truth;
    a non-empty ``cost_summary`` MUST be derivable from it.

    Fields:
      * ``total_calls`` -- ``len(llm_audit)`` at write time.
      * ``total_tokens_in`` / ``total_tokens_out`` -- sums.
      * ``total_latency_ms`` -- sum of per-call latencies.
      * ``total_errors`` -- count of audit entries whose ``error`` is
        non-empty (i.e. the seam exhausted its retry budget).
      * ``by_phase`` -- per-phase ``{phase: {calls, tokens_in,
        tokens_out, errors}}`` breakdown. Each phase entry is a typed
        sub-record so the consumer cannot accidentally read ``tokens``
        as ``calls``.
    """

    model_config = ConfigDict(extra="forbid")

    total_calls: Annotated[int, Field(ge=0)]
    total_tokens_in: Annotated[int, Field(ge=0)]
    total_tokens_out: Annotated[int, Field(ge=0)]
    total_latency_ms: Annotated[int, Field(ge=0)]
    total_errors: Annotated[int, Field(ge=0)]
    by_phase: Dict[str, "PhaseCounters"] = Field(default_factory=dict)


class PhaseCounters(BaseModel):
    """Per-phase breakdown row inside :class:`CostSummary.by_phase`."""

    model_config = ConfigDict(extra="forbid")

    calls: Annotated[int, Field(ge=0)]
    tokens_in: Annotated[int, Field(ge=0)]
    tokens_out: Annotated[int, Field(ge=0)]
    errors: Annotated[int, Field(ge=0)]


# Pydantic v2 forward-ref resolution for the by_phase dict value.
CostSummary.model_rebuild()


class RunMetadata(BaseModel):
    """Operational metadata for one simulation run.

    Strict, ``extra='forbid'``: any unknown key in the JSON blob trips a
    ValidationError instead of silently dropping the value. Field set
    described in the module docstring above.
    """

    model_config = ConfigDict(extra="forbid")

    # Identifier + provenance -------------------------------------------------
    simulation_id: Annotated[str, Field(min_length=1)]
    created_at: datetime
    persona: Any
    scenario_preset_id: Optional[str] = None

    # Deterministic input fingerprints ---------------------------------------
    # ``scenario_hash`` and ``seed_hash`` are sha256 hex strings derived
    # at run-start (see ``app.seam.zep_mock.scenario_hash`` for the
    # canonical hashing convention). Both are REQUIRED and must be
    # non-empty -- the validator below rejects empty strings explicitly
    # so a regression in the call site (forgetting to compute the hash)
    # surfaces at metadata-construction time rather than producing a
    # silently-empty provenance record.
    scenario_hash: Annotated[str, Field(min_length=1)]
    seed_hash: Annotated[str, Field(min_length=1)]

    # Wall-clock timestamp captured during prepare. Distinct from
    # ``created_at`` (which is start time); pairing both lets
    # downstream analysis correlate prepare-vs-start drift. Required and
    # timezone-aware (validator enforces tz-presence).
    prepared_at: datetime

    # Artifact paths: map of artifact-name -> path RELATIVE to the run
    # dir (e.g. ``"manifest.json": "manifest.json"``). Captures the
    # prepare-time artifact set so a post-hoc consumer can locate every
    # input the run was launched with. REQUIRED + non-empty (validator).
    artifact_paths: Dict[str, str]

    # Artifact hashes: map of artifact-name -> sha256 hex. Populated
    # at start time from the prepare manifest. REQUIRED +
    # non-empty (validator); the manifest writer guarantees a non-empty
    # artifacts list.
    artifact_hashes: Dict[str, str]

    # Mode + scale snapshot --------------------------------------------------
    zep_mode: ZepMode
    scale_caps: ScaleCaps

    # Statistical Workbench snapshot ----------------------------------------
    # Optional for older runs and lanes that do not expose the RawFish
    # workbench, but persisted when the generic lifecycle resolves a
    # run-level statistical config.
    statistical_settings_hash: Optional[str] = None
    statistical_settings_version: Optional[str] = None
    statistical_settings: Dict[str, Any] = Field(default_factory=dict)
    statistical_runtime_overrides: Dict[str, Any] = Field(default_factory=dict)
    statistical_variant_lineage: List[Dict[str, Any]] = Field(default_factory=list)
    persona_salts_resolved: Dict[str, str] = Field(default_factory=dict)
    prior_attachments: Dict[str, Any] = Field(default_factory=dict)
    provenance_per_field: Dict[str, str] = Field(default_factory=dict)
    lock_state_per_field: Dict[str, bool] = Field(default_factory=dict)
    resolved_statistical_config: Dict[str, Any] = Field(default_factory=dict)
    variant_lineage: List[Dict[str, Any]] = Field(default_factory=list)

    # Output paths: map of artifact-name -> path RELATIVE to the run dir
    # (e.g. ``"policy_report.json": "policy_report.json"``). Populated at
    # finalization.
    output_paths: Dict[str, str] = Field(default_factory=dict)

    # Runner task id ---------------------------------------------------------
    # ``None`` during the current transition (no background runner is
    # attached yet); the field exists so the on-disk shape is stable
    # once ``SimulationRunner`` re-attaches.
    runner_task_id: Optional[str] = None

    # Per-LLM-call audit log -------------------------------------------------
    # Typed per-call records; see :class:`LLMAuditEntry` docstring.
    # ``None`` distinguishes "audit not collected for this run" from "no
    # calls happened" (which would be ``[]``).
    llm_audit: Optional[List[LLMAuditEntry]] = None

    # Post-run cost / latency / error aggregation ----------------------------
    # ``cost_summary`` is the read-side cache; ``llm_audit`` is the
    # source of truth. Both populated at run-completion time; absent
    # while the run is still in flight.
    cost_summary: Optional[CostSummary] = None

    # Post-run error + warning aggregation. Empty lists by default so a
    # consumer can iterate without a None check. Strings (not structured
    # records) to keep the format human-readable in run_metadata.json;
    # the canonical structured-error envelope is on the API response,
    # not on the on-disk metadata.
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Provenance validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _reject_empty_provenance(self) -> "RunMetadata":
        """Reject empty / whitespace-only provenance strings.

        ``Field(min_length=1)`` on ``scenario_hash`` / ``seed_hash``
        catches the top-level field but does NOT walk dict *values*; a
        caller could pass ``artifact_hashes={"manifest.json": ""}`` and
        still satisfy Pydantic's structural check. This validator closes
        that hole and additionally rejects all-whitespace strings (which
        would also slip through ``min_length=1``). The same walk runs on
        ``artifact_paths`` -- an artifact entry with an empty path is
        just as broken as one with an empty hash.
        """
        if not self.scenario_hash.strip():
            raise ValueError("scenario_hash must be a non-empty string")
        if not self.seed_hash.strip():
            raise ValueError("seed_hash must be a non-empty string")
        if not self.artifact_paths:
            raise ValueError("artifact_paths must be a non-empty mapping")
        if not self.artifact_hashes:
            raise ValueError("artifact_hashes must be a non-empty mapping")
        for name, path in self.artifact_paths.items():
            if not name.strip() or not path.strip():
                raise ValueError(
                    f"artifact_paths entry has empty name or path: "
                    f"{name!r} -> {path!r}"
                )
        for name, digest in self.artifact_hashes.items():
            if not name.strip() or not digest.strip():
                raise ValueError(
                    f"artifact_hashes entry has empty name or digest: "
                    f"{name!r} -> {digest!r}"
                )
        # ``prepared_at`` must be timezone-aware so persistence + diffing
        # stay unambiguous. Pydantic accepts both naive and aware
        # datetimes by default; we tighten the contract here.
        if self.prepared_at.tzinfo is None:
            raise ValueError("prepared_at must be timezone-aware")
        return self

    # ------------------------------------------------------------------
    # Transitional constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_policy_dict(cls, d: Dict[str, Any], **overrides: Any) -> "RunMetadata":
        """Build a :class:`RunMetadata` from the historical inline dict.

        The historical dict shape (before the typed model) had:
        ``simulation_id``, ``created_at`` (ISO string), ``agents``,
        ``rounds``, ``ensemble_n``, ``zep_mode``, ``expensive_override``,
        plus a spread of ``_rough_cost_estimate`` keys
        (``base_tokens_per_call``, ``total_llm_calls``,
        ``est_total_tokens``, ``method``).

        This constructor accepts that legacy bag and surfaces it as the
        typed model. The cost estimate keys + ``expensive_override`` +
        the loose ``agents`` int are intentionally NOT first-class
        fields on :class:`RunMetadata`; they belong on the cost/audit
        sub-models that downstream work introduces. Callers MUST supply
        the new required fields explicitly via ``**overrides``: in
        particular ``persona``, ``scale_caps``, ``scenario_hash``,
        ``seed_hash``, ``prepared_at``, ``artifact_paths``, and
        ``artifact_hashes``. The legacy dict did not carry any of
        these.

        Example::

            RunMetadata.from_policy_dict(
                legacy_dict,
                persona=scenario.persona,
                scale_caps=ScaleCaps(
                    ensemble_n=ensemble_n,
                    stakeholders=len(scenario.stakeholders),
                    rounds=rounds,
                    cap_value=cap_value,
                ),
            )
        """
        # Pluck the keys the typed model accepts; everything else is
        # transitional cost-estimate noise that lives on the response
        # envelope, not on the persisted metadata.
        raw_created = d.get("created_at")
        created_at: datetime
        if isinstance(raw_created, datetime):
            created_at = raw_created
        elif isinstance(raw_created, str) and raw_created:
            # ``datetime.fromisoformat`` handles the ``_utcnow()`` output
            # (timezone-aware ISO 8601) verbatim. No fallback: a
            # malformed string SHOULD trip ValidationError.
            created_at = datetime.fromisoformat(raw_created)
        else:
            created_at = datetime.now(timezone.utc)

        payload: Dict[str, Any] = {
            "simulation_id": d["simulation_id"],
            "created_at": created_at,
            "zep_mode": d["zep_mode"],
        }
        payload.update(overrides)
        return cls.model_validate(payload)


def refresh_output_path(
    run_dir: "os.PathLike[str] | str",
    artifact_name: str,
    sha256_hex: str,
    relative_path: Optional[str] = None,
) -> bool:
    """Atomically refresh ``output_paths[name]`` + ``artifact_hashes[name]``.

    Canonical write path for any route handler that persists an
    artifact next to ``state.json`` and wants the run's
    ``run_metadata.json`` to reflect the new artifact's location +
    sha256 digest.

    Contract:
      * ``run_metadata.json`` MISSING in ``run_dir``  -> return ``False``
        without raising. /prepare is what writes the file; an early
        callsite that fires before prepare legitimately has no record
        to update.
      * ``run_metadata.json`` EXISTS, parse FAILS  -> raise
        :class:`pydantic.ValidationError` unchanged. Corrupt metadata is
        a real bug; suppression here would let stale records persist
        next to fresh artifacts. The route handler maps this to
        ``REPORT_CORRUPTED`` (the canonical error for "the persisted
        record disagrees with reality").
      * Write FAILS  -> propagate the OSError. Route handler maps to
        ``INTERNAL_ERROR`` with a route-specific reason.
      * SUCCESS  -> return ``True``.

    The write is atomic (``backend/app/utils/atomic_io.py::atomic_write_text``
    behind the scenes -- imported here lazily to avoid a circular
    import between ``app.policy`` and ``app.utils``).

    Args:
      run_dir: directory containing ``state.json`` + (optionally)
        ``run_metadata.json``.
      artifact_name: key in ``output_paths`` / ``artifact_hashes``.
      sha256_hex: digest of the newly-persisted artifact.
      relative_path: optional override for the in-dir path; defaults to
        ``artifact_name`` (matches the convention every existing
        artifact uses).
    """
    # Lazy imports to avoid a hard circular dependency at module-load.
    import os as _os
    from pathlib import Path

    from ..utils.atomic_io import atomic_write_text

    run_dir_path = Path(_os.fspath(run_dir))
    meta_path = run_dir_path / "run_metadata.json"
    if not meta_path.exists():
        return False
    with _run_metadata_rmw_lock(run_dir_path):
        existing_raw = meta_path.read_text(encoding="utf-8")
        existing_meta = RunMetadata.model_validate_json(existing_raw)
        output_paths = dict(existing_meta.output_paths)
        artifact_hashes = dict(existing_meta.artifact_hashes)
        output_paths[artifact_name] = relative_path or artifact_name
        artifact_hashes[artifact_name] = sha256_hex
        updated = existing_meta.model_copy(update={
            "output_paths": output_paths,
            "artifact_hashes": artifact_hashes,
        })
        atomic_write_text(meta_path, updated.model_dump_json(indent=2))
    return True


def refresh_audit_entry(
    run_dir: "os.PathLike[str] | str",
    entry: LLMAuditEntry,
) -> bool:
    """Atomically append ``entry`` to ``llm_audit`` in ``run_metadata.json``.

    Canonical write path for any decorator that captures a per-call
    :class:`LLMAuditEntry` and wants it persisted next to the run's
    other metadata.

    Contract (mirrors :func:`refresh_output_path`):
      * ``run_metadata.json`` MISSING in ``run_dir``  -> return ``False``
        without raising. The metadata file is created at /prepare; an
        audit call that fires before prepare legitimately has no record
        to update and the entry is silently dropped.
      * ``run_metadata.json`` EXISTS, parse FAILS  -> raise
        :class:`pydantic.ValidationError` unchanged. Corrupt metadata is
        a real bug; suppression here would let stale records persist
        next to fresh artifacts.
      * Write FAILS  -> propagate the OSError.
      * SUCCESS  -> return ``True``.

    Semantics of the append:
      * ``llm_audit is None``  -> becomes ``[entry]`` (collection
        starts here).
      * ``llm_audit`` is a list -> becomes ``llm_audit + [entry]``
        (existing list is never mutated in place; we ``model_copy``
        with the new list so atomic-write semantics hold even if a
        reader is mid-read).

    Atomicity is provided by
    :func:`app.utils.atomic_io.atomic_write_text` (imported lazily to
    avoid a circular import between ``app.policy`` and ``app.utils``).
    """
    import os as _os
    from pathlib import Path

    from ..utils.atomic_io import atomic_write_text

    run_dir_path = Path(_os.fspath(run_dir))
    meta_path = run_dir_path / "run_metadata.json"
    if not meta_path.exists():
        return False
    with _run_metadata_rmw_lock(run_dir_path):
        existing_raw = meta_path.read_text(encoding="utf-8")
        existing_meta = RunMetadata.model_validate_json(existing_raw)
        prior = list(existing_meta.llm_audit or [])
        prior.append(entry)
        updated = existing_meta.model_copy(update={"llm_audit": prior})
        atomic_write_text(meta_path, updated.model_dump_json(indent=2))
    return True


def refresh_cost_summary(
    run_dir: "os.PathLike[str] | str",
) -> bool:
    """Atomically refresh ``cost_summary`` in ``run_metadata.json``.

    Loads the current ``run_metadata.json`` from ``run_dir``, derives a
    fresh :class:`CostSummary` from its ``llm_audit`` list via
    :func:`summarise_llm_audit`, and writes the updated record back via
    :func:`app.utils.atomic_io.atomic_write_text` (tempfile +
    ``os.replace``).

    Contract (mirrors :func:`refresh_audit_entry`):
      * ``run_metadata.json`` MISSING in ``run_dir``  -> return ``False``
        without raising. A caller that fires before /prepare has no
        record to update.
      * ``run_metadata.json`` EXISTS, parse FAILS  -> raise
        :class:`pydantic.ValidationError` unchanged. Corrupt metadata is
        a real bug; suppression here would let stale records persist
        next to fresh artifacts.
      * Write FAILS  -> propagate the OSError.
      * SUCCESS  -> return ``True``.

    Semantics:
      * ``llm_audit is None`` or ``[]``  -> ``cost_summary`` is set to a
        zero-valued :class:`CostSummary` (every counter at 0, ``by_phase``
        empty). The read-side cache is still populated so a consumer
        sees ``total_calls == 0`` rather than ``cost_summary is None``.
      * Non-empty audit list  -> ``cost_summary`` is the deterministic
        aggregation of every entry.
    """
    import os as _os
    from pathlib import Path

    from ..utils.atomic_io import atomic_write_text

    run_dir_path = Path(_os.fspath(run_dir))
    meta_path = run_dir_path / "run_metadata.json"
    if not meta_path.exists():
        return False
    with _run_metadata_rmw_lock(run_dir_path):
        existing_raw = meta_path.read_text(encoding="utf-8")
        existing_meta = RunMetadata.model_validate_json(existing_raw)
        entries = list(existing_meta.llm_audit or [])
        summary = summarise_llm_audit(entries)
        updated = existing_meta.model_copy(update={"cost_summary": summary})
        atomic_write_text(meta_path, updated.model_dump_json(indent=2))
    return True


def refresh_warning(
    run_dir: "os.PathLike[str] | str",
    warning: str,
) -> bool:
    """Atomically append ``warning`` to ``warnings`` in ``run_metadata.json``.

    Canonical write path for any post-prepare step that surfaces a
    soft demotion / advisory message and wants it persisted next to
    the run's other metadata. Used by the report generator's
    evidence-binding loop so a per-claim demotion warning lands on
    disk immediately rather than waiting for the eventual
    ``generate_and_persist`` metadata refresh -- a crash between
    the in-memory append and the final write would otherwise lose
    the binding-failure record.

    Contract (mirrors :func:`refresh_audit_entry` and
    :func:`refresh_cost_summary`):
      * ``run_metadata.json`` MISSING in ``run_dir``  -> return ``False``
        without raising. The metadata file is created at /prepare; a
        warning emitted before prepare legitimately has no record to
        update and is silently dropped.
      * ``run_metadata.json`` EXISTS, parse FAILS  -> raise
        :class:`pydantic.ValidationError` unchanged.
      * Write FAILS  -> propagate the OSError.
      * SUCCESS  -> return ``True``.

    Idempotency: duplicate warnings (same exact string already in the
    list) are skipped to keep ``run_metadata.warnings`` stable across
    re-runs of the binding loop (e.g. an idempotent
    ``generate_and_persist`` retry must not produce a list whose
    length grows linearly with the retry count).
    """
    import os as _os
    from pathlib import Path

    from ..utils.atomic_io import atomic_write_text

    run_dir_path = Path(_os.fspath(run_dir))
    meta_path = run_dir_path / "run_metadata.json"
    if not meta_path.exists():
        return False
    with _run_metadata_rmw_lock(run_dir_path):
        existing_raw = meta_path.read_text(encoding="utf-8")
        existing_meta = RunMetadata.model_validate_json(existing_raw)
        prior = list(existing_meta.warnings or [])
        if warning in prior:
            # Idempotent: nothing to write; the warning is already on disk.
            return True
        prior.append(warning)
        updated = existing_meta.model_copy(update={"warnings": prior})
        atomic_write_text(meta_path, updated.model_dump_json(indent=2))
    return True


def summarise_llm_audit(entries: List[LLMAuditEntry]) -> CostSummary:
    """Derive a :class:`CostSummary` from a list of :class:`LLMAuditEntry`.

    Pure: no IO, no clock, no state. The caller is responsible for
    deciding when to call this (typically at run-completion time) and
    where to persist the result (typically into
    ``RunMetadata.cost_summary``).
    """
    total_calls = len(entries)
    total_tokens_in = sum(e.tokens_in for e in entries)
    total_tokens_out = sum(e.tokens_out for e in entries)
    total_latency_ms = sum(e.latency_ms for e in entries)
    total_errors = sum(1 for e in entries if e.error)
    by_phase: Dict[str, PhaseCounters] = {}
    for e in entries:
        if e.phase not in by_phase:
            by_phase[e.phase] = PhaseCounters(calls=0, tokens_in=0, tokens_out=0, errors=0)
        prev = by_phase[e.phase]
        by_phase[e.phase] = PhaseCounters(
            calls=prev.calls + 1,
            tokens_in=prev.tokens_in + e.tokens_in,
            tokens_out=prev.tokens_out + e.tokens_out,
            errors=prev.errors + (1 if e.error else 0),
        )
    return CostSummary(
        total_calls=total_calls,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        total_latency_ms=total_latency_ms,
        total_errors=total_errors,
        by_phase=by_phase,
    )


__all__ = [
    "CostSummary",
    "LLMAuditEntry",
    "Persona",
    "PhaseCounters",
    "RunMetadata",
    "RunPersona",
    "ScaleCaps",
    "ZepMode",
    "refresh_audit_entry",
    "refresh_cost_summary",
    "refresh_output_path",
    "refresh_warning",
    "summarise_llm_audit",
]
