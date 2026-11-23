"""Auto-report watcher -- guarded background poller for completed runs.

The runner subprocess writes terminal markers into ``run_state.json`` but
never calls back into the HTTP layer; clients normally invoke
the generic report-generation endpoint themselves once a run reaches
``completed``. Operators that want the report produced
automatically can opt in via the ``AUTO_REPORT_GENERATE=true``
environment variable -- when set, the Flask app factory starts a daemon
thread that scans every run directory under
``Config.OASIS_SIMULATION_DATA_DIR`` and fires the registered
use-case adapter's ``generate_report`` path for every eligible run.

Use-case dispatch
-----------------

The watcher is parameterised by a registered ``use_case_id``. Each poll cycle:

  * Resolves the adapter via :mod:`app.simulation_platform.registry`.
  * Reads the per-adapter report filename from
    :meth:`SimulationUseCase.artifact_names` (key ``"report_json"``);
    the eligibility filter skips run dirs that already hold this file.
  * Rehydrates the persisted scenario via
    :meth:`SimulationUseCase.rehydrate_scenario`.
  * Calls :meth:`SimulationUseCase.generate_report` and persists the
    returned report to ``<run_dir>/<report_filename>`` via the same
    atomic-write primitive the rest of the platform uses.

The watcher is deliberately stateless. Every poll cycle rebuilds its
view from disk so the watcher survives a backend restart without any
in-memory bookkeeping. The trigger condition is the conjunction of:

  * ``state.json`` (or reconciled ``run_state.json``) reports
    ``completed`` as the effective status, AND
  * the adapter's report-json artifact does NOT yet exist in the run
    directory, AND
  * no ``.auto_report.lock`` sentinel is currently held for the
    simulation id.

Lock acquisition uses ``os.open(..., O_CREAT | O_EXCL | O_WRONLY)`` so
two concurrent poll cycles can never both fire generation for the same
simulation. A try/finally always removes the lock on success or
exception so a transient generator failure does not wedge the watcher
forever. A lock left behind by a hard process crash will block future
auto-generates for the affected simulation; an operator removes the
sentinel out of band, which is the documented escape hatch.

Env var contract
----------------

The watcher activates when ``AUTO_REPORT_GENERATE`` resolves truthy.

Public surface:

* :class:`AutoReportWatcher` -- the poller. Instantiate with an
  optional ``use_case_id`` + ``data_dir`` + ``generator_factory`` +
  ``poll_interval`` argument.
* :meth:`AutoReportWatcher.run_once` -- execute a single poll cycle
  synchronously. The unit-testable seam.
* :meth:`AutoReportWatcher.start` / :meth:`AutoReportWatcher.stop` --
  daemon-thread lifecycle for the production app boot path.
* :func:`is_enabled` -- read the opt-in env var. Hoisted so the app
  factory can gate the ``start()`` call without importing the env
  parsing rules.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger("rawfish.simulation_platform.auto_report_watcher")


# ---------------------------------------------------------------------------
# Env opt-in
# ---------------------------------------------------------------------------

# Canonical (post-generalisation) opt-in. Read at call time so a test
# can flip the env between cases without reloading the module.
_ENV_VAR = "AUTO_REPORT_GENERATE"
# Default use-case id when the caller does not pass one. Matches the
# first policy lane registered in RawFish, so a bare
# ``start_if_enabled()`` call selects a real product lane.
DEFAULT_USE_CASE_ID = "internal_policy"

# Per-sim sentinel filename for the auto-report critical section. Exposed
# (no leading underscore on the public alias below) so other callers --
# notably the manual ``POST /report/generate`` route -- can participate in
# the same lock and avoid double-generation races.
_LOCK_FILENAME = ".auto_report.lock"
AUTO_REPORT_LOCK_FILENAME = _LOCK_FILENAME
_STATE_FILENAME = "state.json"
_RUN_STATE_FILENAME = "run_state.json"
_FAILURE_BASE_DELAY_SECONDS = 60
_FAILURE_MAX_DELAY_SECONDS = 900
_TERMINAL_FAILURE_REASONS = frozenset({
    "adapter_unregistered",
    "artifact_name_missing",
    "artifact_name_unsafe",
    "generate_report_unsupported",
})


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _is_safe_basename_artifact(name: object) -> bool:
    """Whitelist-validate an adapter ``artifact_names`` entry.

    Mirrors :func:`app.api.use_cases._safe_basename_artifact` so the
    watcher refuses to write to a path the adapter could otherwise
    use to escape the run directory (an attacker who controls the
    adapter registration could declare ``../../escape.json`` and the
    watcher would persist OUTSIDE ``run_dir``). The watcher path
    bypasses the manual route's check, so the validation lives here
    too.

    Returns True iff ``name`` is a non-empty string with no path
    separator, no absolute prefix, and is neither ``.`` nor ``..``.
    """
    if not isinstance(name, str) or not name:
        return False
    if "/" in name or os.sep in name or name.startswith("/"):
        return False
    if name in {".", ".."}:
        return False
    return True


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_enabled() -> bool:
    """True iff the auto-report watcher is opted in via env.

    Read at call time (not module import time) so the test harness can
    flip the env var between cases without reloading the module.
    Accepted truthy values are ``true`` / ``1`` / ``yes`` / ``on``
    (case-insensitive).
    """
    raw = os.environ.get(_ENV_VAR, "")
    if raw and _truthy(raw):
        return True
    return False


# ---------------------------------------------------------------------------
# Generator factory seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationOutcome:
    """Return shape of a generator factory invocation.

    Carries only the per-run signal the watcher logs. The on-disk
    artifacts the factory wrote are the authoritative output; this
    struct exists so the poll loop can record success / skip / failure
    without re-reading the directory.
    """

    simulation_id: str
    report_written: bool
    reason: Optional[str] = None


class GeneratorFactory(Protocol):
    """Callable that produces + persists a report for a single run dir.

    Implementations are responsible for the full generator construction
    path (LLM client wiring, scenario rehydration, run_metadata load,
    adapter ``generate_report`` call, atomic write to disk, optional
    post-generation refreshes). The watcher invokes the factory once
    per eligible run dir under a held lock and consumes only the
    returned :class:`GenerationOutcome`.

    Signature:
      * ``simulation_id`` -- the run id the watcher is acting on.
      * ``run_dir`` -- absolute path to the simulation's run directory.
      * ``use_case_id`` -- the registered adapter the watcher resolved;
        the factory uses this to look up the adapter via the platform
        registry and read its ``artifact_names`` / ``generate_report``
        seam.
    """

    def __call__(
        self,
        *,
        simulation_id: str,
        run_dir: Path,
        use_case_id: str,
    ) -> GenerationOutcome: ...


# ---------------------------------------------------------------------------
# Production generator factory
# ---------------------------------------------------------------------------


def production_generator_factory(
    *,
    simulation_id: str,
    run_dir: Path,
    use_case_id: str,
) -> GenerationOutcome:
    """Build the production LLM stack and run the adapter ``generate_report``.

    Generic across use cases: looks up the adapter via the platform
    registry by ``use_case_id``, rehydrates the scenario via the
    adapter's :meth:`rehydrate_scenario` Protocol method, loads the
    persisted :class:`RunMetadata`, then calls
    :meth:`SimulationUseCase.generate_report`. The returned report is
    serialised to JSON and atomically written to
    ``<run_dir>/<artifact_names()["report_json"]>``.

    The adapter's generic ``generate_report`` is documented as an
    in-memory call (NOT a persist call) so the watcher owns the
    disk-side write. The watcher matches the bundle the manual
    ``POST /api/sim/use-cases/<id>/report/generate`` route produces:

      * the canonical JSON artifact under
        ``artifact_names()["report_json"]`` (always),
      * the rendered markdown under
        ``artifact_names()["report_md"]`` when the adapter wires
        ``guardrail_capabilities.render_markdown``,
      * a ``refresh_output_path`` call for each artifact so
        ``run_metadata.output_paths`` / ``artifact_hashes`` carry
        canonical pointers + sha256 digests after the watcher emits.

    The guardrail capability hook pipeline runs in the same pinned
    order the manual route uses
    (``build_evidence_catalog`` -> adapter.generate_report ->
    ``run_advisory_claim_pass`` -> ``apply_transparency_flags`` ->
    ``render_markdown`` -> ``assert_render_contract``). Each hook is
    opt-in via the adapter's ``guardrail_capabilities``; a missing
    hook is skipped honestly (no silent fallback). A hook that
    raises records the corresponding ``reason`` on the
    :class:`GenerationOutcome` and absorbs the failure -- the watcher
    NEVER propagates out of this function so a single bad run cannot
    crash the poll loop.

    Returns a :class:`GenerationOutcome` with ``report_written=False``
    and a populated ``reason`` for every skip/failure path -- the
    watcher never raises out of this function so a single bad run
    cannot crash the poll loop.

    Skips (``report_written=False``):
      * ``run_metadata_missing`` -- ``run_metadata.json`` not on disk.
      * ``state_unreadable`` -- ``state.json`` cannot be read / parsed.
      * ``scenario_revalidation_failed`` -- rehydration raised.
      * ``run_metadata_corrupt`` -- ``run_metadata.json`` exists but
        fails ``RunMetadata.model_validate_json``.
      * ``adapter_unregistered`` -- ``use_case_id`` is not in the registry.
      * ``artifact_name_missing`` -- adapter does not declare a
        ``report_json`` entry in :meth:`artifact_names`.

    Failures (``report_written=False``):
      * ``guardrail_evidence_catalog_failed`` -- the adapter's
        ``build_evidence_catalog`` hook raised.
      * ``generator_raised`` -- any unexpected exception out of the
        generator stack; the exception is logged with a stack trace and
        absorbed so the watcher keeps polling.
      * ``guardrail_advisory_pass_failed`` -- the adapter's
        ``run_advisory_claim_pass`` hook raised.
      * ``guardrail_transparency_failed`` -- the adapter's
        ``apply_transparency_flags`` hook raised.
      * ``guardrail_markdown_render_failed`` -- the adapter's
        ``render_markdown`` hook raised.
      * ``render_contract_violation`` -- ``assert_render_contract``
        raised against the rendered markdown.
      * ``report_serialisation_failed`` -- the report payload could
        not be serialised to JSON.
      * ``generated_report_schema_invalid`` -- the serialised payload
        failed ``adapter.report_schema.model_validate_json`` so the
        write was rejected before the bytes landed on disk.
      * ``report_write_failed`` -- the atomic-write itself raised.
      * ``markdown_write_failed`` -- the markdown atomic-write raised.
      * ``output_paths_refresh_failed`` -- the post-write
        ``refresh_output_path`` raised (rare; the report itself is on
        disk in this case but ``run_metadata.output_paths`` was not
        refreshed to point at it).
    """
    # Lazy imports so the watcher module itself is import-safe even
    # when the heavyweight LLM / scenario stack is not available
    # (notably during early-boot test collection).
    import hashlib

    from ..simulation_platform.run_metadata import (
        RunMetadata,
        refresh_cost_summary,
        refresh_output_path,
    )
    from ..simulation_platform import registry as _registry
    from ..simulation_platform.llm import (
        AuditedLLMClient,
        RunMetadataAuditSink,
        StructuredOutputClient,
    )
    from ..simulation_platform.llm.provider import (
        build_llm_client_for_run,
    )
    from ..simulation_platform.llm.router import (
        check_provider_readiness,
        provider_config_from_state,
    )
    from ..utils.atomic_io import atomic_write_text
    from pydantic import ValidationError

    try:
        adapter = _registry.get(use_case_id)
    except KeyError:
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="adapter_unregistered",
        )

    artifact_names = adapter.artifact_names()
    report_filename = artifact_names.get("report_json")
    if not report_filename:
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="artifact_name_missing",
        )
    if not _is_safe_basename_artifact(report_filename):
        logger.warning(
            "auto-watcher: artifact_names['report_json']=%r for %s is "
            "not a safe basename; refusing write",
            report_filename, simulation_id,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="artifact_name_unsafe",
        )
    # The markdown filename is also validated up-front (before the
    # report-json write lands) so an adapter that declares a unsafe
    # ``report_md`` cannot wedge the lane with json-written-but-md-
    # blocked state.
    declared_md_filename = artifact_names.get("report_md")
    if declared_md_filename is not None and not _is_safe_basename_artifact(
        declared_md_filename,
    ):
        logger.warning(
            "auto-watcher: artifact_names['report_md']=%r for %s is "
            "not a safe basename; refusing write",
            declared_md_filename, simulation_id,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="artifact_name_unsafe",
        )

    state_path = run_dir / _STATE_FILENAME
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "auto-watcher: state.json unreadable for %s: %s",
            simulation_id, exc,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="state_unreadable",
        )

    try:
        scenario = adapter.rehydrate_scenario(state)
    except (KeyError, ValidationError, ValueError) as exc:
        logger.warning(
            "auto-watcher: scenario rehydration failed for %s: %s",
            simulation_id, exc,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="scenario_revalidation_failed",
        )

    # Lane-aware run_metadata resolution. Mirrors the generic
    # ``/report/generate`` route's contract:
    #
    #   * Adapters that consume the full typed :class:`RunMetadata`
    #     (enum persona, scale_caps, artifact_hashes, ...) declare
    #     ``adapter_runs_guardrails_internally = True``. The watcher
    #     loads + validates ``run_metadata.json`` via
    #     ``RunMetadata.model_validate_json``; missing / corrupt
    #     metadata produces an explicit skip outcome.
    #   * Adapters that read only the four duck-typed attributes
    #     (``simulation_id`` / ``scenario_hash`` / ``seed_hash`` /
    #     ``persona``) flow through a state-derived
    #     :class:`types.SimpleNamespace`. This path is reachable for
    #     lanes that have not minted ``run_metadata.json`` (the
    #     Protocol does not force a metadata-write step), so the
    #     watcher MUST NOT skip on its absence.
    runs_guardrails_internally = bool(
        getattr(adapter, "adapter_runs_guardrails_internally", False),
    )
    run_metadata_path = run_dir / "run_metadata.json"
    if runs_guardrails_internally:
        if not run_metadata_path.exists():
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="run_metadata_missing",
            )
        try:
            run_metadata = RunMetadata.model_validate_json(
                run_metadata_path.read_text(encoding="utf-8"),
            )
        except (OSError, ValidationError) as exc:
            logger.warning(
                "auto-watcher: run_metadata.json invalid for %s: %s",
                simulation_id, exc,
            )
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="run_metadata_corrupt",
            )
    else:
        import hashlib as _hashlib
        from types import SimpleNamespace
        scenario_payload = state.get("scenario") or {}
        raw_scenario = json.dumps(scenario_payload, sort_keys=True).encode(
            "utf-8",
        )
        run_metadata = SimpleNamespace(  # type: ignore[assignment]
            simulation_id=state.get("simulation_id", simulation_id),
            scenario_hash=_hashlib.sha256(raw_scenario).hexdigest(),
            seed_hash=_hashlib.sha256(
                (state.get("simulation_id", simulation_id) + ":seed").encode(
                    "utf-8",
                ),
            ).hexdigest(),
            persona=scenario_payload.get("persona", ""),
        )

    # Guardrail capability discovery + ``build_evidence_catalog``.
    # Mirrors the generic ``/report/generate`` route's pipeline so the
    # auto-emitted bundle matches the manual one: a hook the adapter
    # leaves as ``None`` is skipped honestly, and a hook that raises
    # records the corresponding reason on the outcome.
    caps = getattr(adapter, "guardrail_capabilities", None)
    evidence_catalog = None
    if caps is not None and caps.build_evidence_catalog is not None:
        try:
            evidence_catalog = caps.build_evidence_catalog(run_dir, scenario)
        except Exception as exc:
            logger.exception(
                "auto-watcher: build_evidence_catalog failed for %s: %s",
                simulation_id, exc,
            )
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="guardrail_evidence_catalog_failed",
            )

    try:
        provider_config = provider_config_from_state(state.get("llm_provider"))
        readiness = check_provider_readiness(provider_config, probe=False)
        if not readiness.ready:
            logger.warning(
                "auto-watcher: LLM provider not ready for %s: %s",
                simulation_id,
                readiness.reason or readiness.status,
            )
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="llm_provider_not_ready",
            )
        raw_llm_client = build_llm_client_for_run(
            adapter=adapter,
            provider_config=provider_config,
        )
        # Strict-mode JSON target: prefer the LLM-owned
        # :attr:`report_draft_schema` (the subset the model is
        # actually permitted to emit) over the persisted
        # :attr:`report_schema`. Adapters whose ``generate_report``
        # merges caller-owned provenance into the LLM output BEFORE
        # validating against the persisted schema (policy lane) would
        # have the strict-schema preflight reject every legitimate
        # draft because the provenance fields are not in the LLM
        # payload yet. The draft schema captures exactly what the
        # model is permitted to emit; the persisted schema is
        # enforced downstream by the adapter's own merge +
        # ``model_validate`` step. When neither field is set the
        # wrapper is skipped and the loose-JSON path is used.
        strict_schema = (
            getattr(adapter, "report_draft_schema", None)
            or getattr(adapter, "report_schema", None)
        )
        if strict_schema is not None:
            structured_llm_client = StructuredOutputClient(
                raw_llm_client,  # type: ignore[arg-type]
                target_schema=strict_schema,
                schema_name=f"{use_case_id}_report",
            )
            llm_inner = structured_llm_client
        else:
            llm_inner = raw_llm_client  # type: ignore[assignment]
        llm_client = AuditedLLMClient(
            llm_inner,  # type: ignore[arg-type]
            sink=RunMetadataAuditSink(run_dir=str(run_dir)),
            phase="report",
        )
        # ``llm_client`` is threaded through so the adapter exercises
        # the audit sink + structured-output wrapping configured here
        # rather than constructing its own client from env.
        report = adapter.generate_report(
            scenario=scenario,
            run_metadata=run_metadata,
            evidence_catalog=evidence_catalog,
            llm_client=llm_client,
        )
    except NotImplementedError:
        # Adapters that opt out of generic ``generate_report`` signal
        # that via ``NotImplementedError``. The watcher logs + records
        # the reason and moves on; it does not retry.
        logger.info(
            "auto-watcher: %s adapter does not implement generate_report; "
            "skipping %s",
            use_case_id, simulation_id,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="generate_report_unsupported",
        )
    except Exception as exc:
        # Defence in depth: a generator failure must not kill the
        # poll loop. The route surfaces detailed error envelopes; the
        # watcher only logs + records the reason.
        logger.exception(
            "auto-watcher: generator failed for %s: %s",
            simulation_id, exc,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="generator_raised",
        )

    # Post-generate guardrail hook chain. Pinned order matches the
    # manual ``/report/generate`` route. Each hook is opt-in (the
    # adapter declares it on ``guardrail_capabilities``); a missing
    # hook is skipped honestly; a hook that raises records the
    # appropriate reason and absorbs the failure.
    #
    # Adapter-internal-guardrails gate: lanes whose ``generate_report``
    # already drives advisory / transparency / language-contract
    # passes internally declare
    # :attr:`SimulationUseCase.adapter_runs_guardrails_internally` as
    # ``True``. The watcher then SKIPS the post-generate hooks below
    # so the side effects (warnings, demotions, validity-flag
    # synthesis) do not compound on the same report. The
    # ``build_evidence_catalog`` hook ran BEFORE the adapter call
    # (the adapter consumes it as an input) and is not part of this
    # skip.
    runs_guardrails_internally = bool(
        getattr(adapter, "adapter_runs_guardrails_internally", False),
    )
    rendered_markdown: Optional[str] = None
    if not runs_guardrails_internally and (
        caps is not None and caps.run_advisory_claim_pass is not None
    ):
        try:
            report = caps.run_advisory_claim_pass(report, evidence_catalog)
        except Exception as exc:
            logger.exception(
                "auto-watcher: run_advisory_claim_pass failed for %s: %s",
                simulation_id, exc,
            )
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="guardrail_advisory_pass_failed",
            )
    if not runs_guardrails_internally and (
        caps is not None and caps.apply_transparency_flags is not None
    ):
        try:
            report = caps.apply_transparency_flags(report, run_metadata)
        except Exception as exc:
            logger.exception(
                "auto-watcher: apply_transparency_flags failed for %s: %s",
                simulation_id, exc,
            )
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="guardrail_transparency_failed",
            )
    if not runs_guardrails_internally and (
        caps is not None and caps.render_markdown is not None
    ):
        try:
            rendered_markdown = caps.render_markdown(report)
        except Exception as exc:
            logger.exception(
                "auto-watcher: render_markdown failed for %s: %s",
                simulation_id, exc,
            )
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="guardrail_markdown_render_failed",
            )
        if caps.assert_render_contract is not None:
            try:
                caps.assert_render_contract(rendered_markdown)
            except Exception as exc:
                logger.exception(
                    "auto-watcher: assert_render_contract failed for %s: %s",
                    simulation_id, exc,
                )
                return GenerationOutcome(
                    simulation_id=simulation_id,
                    report_written=False,
                    reason="render_contract_violation",
                )

    # Serialise the adapter-returned report. We accept either a
    # Pydantic model (use ``model_dump_json``) or any JSON-serialisable
    # object (fall back to ``json.dumps``); adapters today return a
    # Pydantic model but the platform Protocol leaves the type ``Any``.
    try:
        if hasattr(report, "model_dump_json"):
            payload = report.model_dump_json(indent=2)
        else:
            payload = json.dumps(report, indent=2, default=str)
    except (TypeError, ValueError) as exc:
        logger.exception(
            "auto-watcher: report serialisation failed for %s: %s",
            simulation_id, exc,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="report_serialisation_failed",
        )

    # Write-time schema gate. Mirrors the generic ``/report/generate``
    # route: ``adapter.report_schema`` (Protocol member, can be None)
    # is the same model the read route uses, so a payload that fails
    # here would also fail there. Failing early keeps the corrupt
    # bytes off disk + records the discriminator so the watcher's
    # outcome stream is debuggable.
    write_schema = getattr(adapter, "report_schema", None)
    if write_schema is not None and hasattr(write_schema, "model_validate_json"):
        try:
            write_schema.model_validate_json(payload)
        except ValidationError as exc:
            logger.warning(
                "auto-watcher: generated report failed adapter.report_schema "
                "for %s: %s",
                simulation_id, exc,
            )
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="generated_report_schema_invalid",
            )

    try:
        atomic_write_text(str(run_dir / report_filename), payload)
    except OSError as exc:
        logger.exception(
            "auto-watcher: report write failed for %s: %s",
            simulation_id, exc,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="report_write_failed",
        )
    json_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # Markdown write (when the adapter wired ``render_markdown``).
    md_filename: Optional[str] = None
    md_sha: Optional[str] = None
    if rendered_markdown is not None:
        #: Resolution of the markdown filename: the adapter's
        #: declared name (already basename-checked above), or the
        #: ``report.md`` default. The default is a literal basename
        #: so no further check is needed.
        md_filename = declared_md_filename or "report.md"
        try:
            atomic_write_text(str(run_dir / md_filename), rendered_markdown)
        except OSError as exc:
            logger.exception(
                "auto-watcher: markdown write failed for %s: %s",
                simulation_id, exc,
            )
            return GenerationOutcome(
                simulation_id=simulation_id,
                report_written=False,
                reason="markdown_write_failed",
            )
        md_sha = hashlib.sha256(
            rendered_markdown.encode("utf-8"),
        ).hexdigest()

    # Refresh ``run_metadata.output_paths`` / ``artifact_hashes`` so
    # consumers reading the metadata see canonical pointers + digests
    # after the watcher emits. Mirrors the manual route's contract.
    try:
        refresh_output_path(
            run_dir,
            artifact_name=report_filename,
            sha256_hex=json_sha,
            relative_path=report_filename,
        )
        if md_filename is not None and md_sha is not None:
            refresh_output_path(
                run_dir,
                artifact_name=md_filename,
                sha256_hex=md_sha,
                relative_path=md_filename,
            )
    except Exception as exc:
        logger.exception(
            "auto-watcher: output_paths refresh failed for %s: %s",
            simulation_id, exc,
        )
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason="output_paths_refresh_failed",
        )

    try:
        refresh_cost_summary(str(run_dir))
    except Exception:
        logger.exception(
            "auto-watcher: refresh_cost_summary failed for %s",
            simulation_id,
        )

    #: Lifecycle write-back. The manual generic ``/report/generate``
    #: route advances ``state.json[phase]=status="reported"`` AND
    #: persists the report path/hash on the same state object so a
    #: subsequent ``/status`` poll and a re-read of the catalog see
    #: the terminal lifecycle state. The watcher previously emitted
    #: artifacts only and left ``state.json`` at ``completed``, which
    #: made auto-generated runs look manually-reportable: the
    #: ``/report/generate`` gate above would re-admit the run and a
    #: manual click would attempt a duplicate generation against the
    #: already-written artifacts. Mirror the route's write-back here
    #: so the lifecycle truth is the same regardless of which path
    #: emitted the report. Best-effort: a failure to update
    #: state.json does NOT roll back the on-disk artifacts (those
    #: are the load-bearing output); the failure is logged so the
    #: operator sees the lifecycle drift.
    try:
        state_path = run_dir / _STATE_FILENAME
        if state_path.is_file():
            with open(state_path, "r", encoding="utf-8") as fh:
                state_for_writeback = json.load(fh)
            state_for_writeback["phase"] = "reported"
            state_for_writeback["status"] = "reported"
            state_for_writeback["report_path"] = report_filename
            state_for_writeback["report_sha256"] = f"sha256:{json_sha}"
            if md_filename is not None and md_sha is not None:
                state_for_writeback["report_md_path"] = md_filename
                state_for_writeback["report_md_sha256"] = (
                    f"sha256:{md_sha}"
                )
            from ..utils.atomic_io import atomic_write_json
            atomic_write_json(str(state_path), state_for_writeback)
    except Exception:  # noqa: BLE001
        logger.exception(
            "auto-watcher: state.json write-back to 'reported' failed "
            "for %s; the report artifacts are on disk but the "
            "lifecycle marker is stale",
            simulation_id,
        )

    return GenerationOutcome(
        simulation_id=simulation_id,
        report_written=True,
    )


def _read_state_dict(run_dir: Path) -> Dict[str, object]:
    try:
        payload = json.loads((run_dir / _STATE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _auto_report_failure_blocks(run_dir: Path) -> Optional[str]:
    state = _read_state_dict(run_dir)
    marker = state.get("auto_report_failure")
    if not isinstance(marker, dict):
        return None
    if marker.get("terminal") is True:
        return str(marker.get("last_reason") or "terminal_failure")
    next_retry_at = _parse_timestamp(marker.get("next_retry_at"))
    if next_retry_at is None:
        return None
    if next_retry_at > datetime.now(timezone.utc):
        return str(marker.get("last_reason") or "backoff")
    return None


def _record_auto_report_outcome(
    run_dir: Path,
    outcome: GenerationOutcome,
) -> None:
    state = _read_state_dict(run_dir)
    if not state:
        return
    if outcome.report_written:
        if "auto_report_failure" not in state:
            return
        state.pop("auto_report_failure", None)
    else:
        reason = outcome.reason or "unknown_failure"
        if reason == "report_generate_in_progress":
            return
        prior = state.get("auto_report_failure")
        prior_attempts = (
            int(prior.get("attempts", 0))
            if isinstance(prior, dict)
            else 0
        )
        attempts = prior_attempts + 1
        terminal = reason in _TERMINAL_FAILURE_REASONS
        delay = min(
            _FAILURE_MAX_DELAY_SECONDS,
            _FAILURE_BASE_DELAY_SECONDS * (2 ** max(0, attempts - 1)),
        )
        next_retry = None if terminal else (
            datetime.now(timezone.utc) + timedelta(seconds=delay)
        ).isoformat()
        state["auto_report_failure"] = {
            "attempts": attempts,
            "last_reason": reason,
            "last_failed_at": _utcnow(),
            "next_retry_at": next_retry,
            "terminal": terminal,
        }
    try:
        from ..utils.atomic_io import atomic_write_json

        atomic_write_json(str(run_dir / _STATE_FILENAME), state)
    except OSError as exc:
        logger.warning(
            "auto-watcher: could not persist failure marker for %s: %s",
            run_dir.name,
            exc,
        )


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class AutoReportWatcher:
    """Polling watcher for completed runs missing the adapter's report.

    Construct with an explicit ``use_case_id`` (defaults to
    :data:`DEFAULT_USE_CASE_ID`), ``data_dir`` (defaults to
    ``Config.OASIS_SIMULATION_DATA_DIR``) and an optional
    ``generator_factory`` (defaults to
    :func:`production_generator_factory`). The class is
    thread-safe in the sense that :meth:`run_once` is reentrant and
    file-locked per simulation id; the daemon thread held by
    :meth:`start` / :meth:`stop` simply calls ``run_once`` on an
    interval.
    """

    def __init__(
        self,
        *,
        use_case_id: str = DEFAULT_USE_CASE_ID,
        data_dir: Optional[str] = None,
        generator_factory: Optional[GeneratorFactory] = None,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._use_case_id = use_case_id
        self._data_dir = data_dir or Config.OASIS_SIMULATION_DATA_DIR
        self._generator_factory: GeneratorFactory = (
            generator_factory or production_generator_factory
        )
        self._poll_interval = max(0.1, float(poll_interval_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Per-cycle report-filename resolution
    # ------------------------------------------------------------------

    def _report_filename(self) -> Optional[str]:
        """Resolve the adapter's report-json filename for the eligibility filter.

        Returns ``None`` when the adapter is not registered or does
        not declare a ``report_json`` artifact -- the watcher treats
        both as "no eligible runs" and quietly idles. The lookup is
        re-done every poll cycle so a lane registered after the
        watcher started picks up automatically.
        """
        from ..simulation_platform import registry as _registry

        try:
            adapter = _registry.get(self._use_case_id)
        except KeyError:
            return None
        return adapter.artifact_names().get("report_json")

    # ------------------------------------------------------------------
    # Eligibility checks
    # ------------------------------------------------------------------

    @staticmethod
    def _effective_status(run_dir: Path, sim_id: str) -> Optional[str]:
        """Reconcile ``state.json`` + ``run_state.json`` into one status.

        Mirrors the precedence the generic ``/status`` and
        ``/report/generate`` routes use:
        ``run_state.json[runner_status]`` wins when it reports a
        terminal value (``completed`` / ``failed``); otherwise
        ``state.json[status]`` is returned verbatim. Returns ``None``
        when neither file is parseable.

        Probes the run dir's own ``run_state.json`` first AND the
        protected runner's flat directory
        (``SimulationRunner.RUN_STATE_DIR/<sim_id>/``) where production
        policy subprocesses write. The watcher previously only read
        the in-run-dir copy, so completed generic policy subprocess
        runs (which write to the flat path only) were never picked
        up for auto-report.
        """
        state_path = run_dir / _STATE_FILENAME
        if not state_path.exists():
            return None
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        status = state.get("status")

        candidate_paths: list[Path] = [run_dir / _RUN_STATE_FILENAME]
        try:
            from ..services.simulation_runner import SimulationRunner
            flat = Path(SimulationRunner.RUN_STATE_DIR) / sim_id / (
                _RUN_STATE_FILENAME
            )
            if flat not in candidate_paths:
                candidate_paths.append(flat)
        except Exception:  # noqa: BLE001
            pass

        for run_state_path in candidate_paths:
            if not run_state_path.exists():
                continue
            try:
                with open(run_state_path, "r", encoding="utf-8") as fh:
                    run_state = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            runner_status = (run_state.get("runner_status") or "").lower()
            if runner_status in ("completed", "failed"):
                return runner_status

        return status

    # ------------------------------------------------------------------
    # Single poll
    # ------------------------------------------------------------------

    def run_once(self) -> list[GenerationOutcome]:
        """Scan the data dir once + fire generation for eligible runs.

        Returns one :class:`GenerationOutcome` per simulation id the
        watcher attempted to act on (skipped entries that did NOT pass
        the eligibility filter at all -- e.g. status != completed --
        are not reported). The opt-in env check is re-evaluated here so
        a test that calls ``run_once`` directly without setting the env
        var sees a no-op return (preserves the "watcher is inert"
        contract even when the daemon thread is not running).
        """
        outcomes: list[GenerationOutcome] = []
        if not is_enabled():
            return outcomes

        if not os.path.isdir(self._data_dir):
            return outcomes

        report_filename = self._report_filename()
        if not report_filename:
            # Adapter not registered (boot ordering) or it does not
            # declare a report_json artifact; idle this cycle.
            return outcomes
        # Basename-safety gate up front so the eligibility path
        # (``(run_dir / report_filename).exists()``) never resolves
        # an unsafe adapter declaration to a probe outside ``run_dir``.
        # An unsafe basename here means no eligible runs for this
        # adapter -- treat as inert until the operator fixes the
        # declaration.
        if not _is_safe_basename_artifact(report_filename):
            logger.warning(
                "auto-watcher: artifact_names['report_json']=%r for "
                "use_case_id=%s is not a safe basename; scan skipped",
                report_filename, self._use_case_id,
            )
            return outcomes

        # Multi-level scan: collect candidate run dirs from both layouts.
        #
        #   1. Namespaced lane layout:
        #      ``<data>/<use_case_id>/<sim_id>/``.
        #   2. Flat runner layout: ``<data>/<sim_id>/``. The watcher
        #      consumes a flat entry ONLY when the run's
        #      ``state.json`` declares ``use_case_id == self._use_case_id``;
        #      otherwise the entry belongs to a different lane and a
        #      sibling watcher (one per lane) will pick it up.
        #
        #   3. Dedup precedence: if the same ``sim_id`` appears in
        #      BOTH layouts, the namespaced run_dir wins -- the flat
        #      copy is treated as a mirror and skipped, so
        #      an operator running both layouts against the same id
        #      never sees double generation.
        candidate_run_dirs: dict[str, Path] = {}
        ns_root = Path(self._data_dir) / self._use_case_id
        if ns_root.is_dir():
            try:
                ns_entries = sorted(os.listdir(ns_root))
            except OSError as exc:
                logger.warning(
                    "auto-watcher: cannot list namespaced dir %s: %s",
                    ns_root, exc,
                )
                ns_entries = []
            for sim_id in ns_entries:
                run_dir = ns_root / sim_id
                if not run_dir.is_dir():
                    continue
                candidate_run_dirs[sim_id] = run_dir

        try:
            flat_entries = sorted(os.listdir(self._data_dir))
        except OSError as exc:
            logger.warning("auto-watcher: cannot list data dir: %s", exc)
            return outcomes
        for sim_id in flat_entries:
            if sim_id in candidate_run_dirs:
                continue  # namespaced wins; skip the flat mirror.
            run_dir = Path(self._data_dir) / sim_id
            if not run_dir.is_dir():
                continue
            # The use-case-id-prefixed dirs are NOT sim_ids -- they
            # are the namespaced parents handled above.
            try:
                from ..simulation_platform import registry as _registry
                _registry.get(sim_id)
                continue
            except KeyError:
                pass
            state_path = run_dir / _STATE_FILENAME
            if state_path.is_file():
                try:
                    with open(state_path, "r", encoding="utf-8") as fh:
                        state = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                state_use_case = state.get("use_case_id")
                # Entries without ``use_case_id`` belong only to the
                # default watcher; lane-specific watchers skip them.
                if state_use_case is None:
                    if self._use_case_id != DEFAULT_USE_CASE_ID:
                        continue
                elif state_use_case != self._use_case_id:
                    continue
                candidate_run_dirs[sim_id] = run_dir
            else:
                # No state.json at the flat path -- not a legacy run
                # dir for this watcher.
                continue

        # Filtered scan: skip any run dir that ALREADY has the
        # adapter's report artifact. The eligibility check inside
        # ``_maybe_generate`` runs again as a correctness backstop;
        # this filter is purely an optimisation that keeps the
        # per-cycle cost at O(unfinished runs).
        for sim_id, run_dir in sorted(candidate_run_dirs.items()):
            if (run_dir / report_filename).exists():
                continue
            outcome = self._maybe_generate(sim_id, run_dir, report_filename)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def _maybe_generate(
        self, sim_id: str, run_dir: Path, report_filename: str,
    ) -> Optional[GenerationOutcome]:
        """Eligibility + lock-guarded single-sim generation."""
        report_path = run_dir / report_filename
        if report_path.exists():
            return None

        status = self._effective_status(run_dir, sim_id)
        if status != "completed":
            return None
        blocked_reason = _auto_report_failure_blocks(run_dir)
        if blocked_reason:
            return GenerationOutcome(
                simulation_id=sim_id,
                report_written=False,
                reason=f"auto_report_backoff:{blocked_reason}",
            )

        lock_path = run_dir / _LOCK_FILENAME
        try:
            # O_CREAT | O_EXCL: atomic first-writer-wins. A second poll
            # racing the first observes EEXIST and skips. The mode 0o600
            # keeps the sentinel readable to the same user only.
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            # Another writer holds the sentinel -- either a concurrent
            # watcher poll or a manual ``POST /report/generate`` route
            # that participates in the same lock. Surface an explicit
            # skip outcome (rather than ``None``) so operators reading
            # the watcher's poll log can tell "skipped because of an
            # in-flight peer" from "no eligible runs this cycle".
            return GenerationOutcome(
                simulation_id=sim_id,
                report_written=False,
                reason="report_generate_in_progress",
            )
        except OSError as exc:
            logger.warning(
                "auto-watcher: cannot create lock for %s: %s", sim_id, exc,
            )
            return None

        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

        try:
            outcome = self._generator_factory(
                simulation_id=sim_id,
                run_dir=run_dir,
                use_case_id=self._use_case_id,
            )
            _record_auto_report_outcome(run_dir, outcome)
            return outcome
        finally:
            try:
                os.unlink(str(lock_path))
            except OSError:
                # The lock was removed out of band or the filesystem
                # transiently failed the unlink. The next poll cycle
                # will observe the report-already-exists path (success)
                # or re-attempt acquisition (clean state).
                pass

    # ------------------------------------------------------------------
    # Daemon thread lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the daemon poll thread (idempotent).

        A second call while the thread is alive is a no-op so the app
        factory can run idempotently across the Werkzeug reloader's two
        boot passes without doubling the polling rate.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="rawfish-auto-report-watcher",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "auto-report watcher started "
            "(use_case_id=%s, data_dir=%s, interval=%.1fs)",
            self._use_case_id, self._data_dir, self._poll_interval,
        )

    def stop(self, timeout: Optional[float] = None) -> None:
        """Signal the loop to exit + optionally join the thread.

        Exposed so tests + graceful-shutdown hooks have a deterministic
        teardown path. A None timeout joins indefinitely; a float waits
        up to that many seconds. The thread reference is dropped only
        when ``thread.is_alive()`` is False AFTER the join -- a
        timed-out join MUST NOT clear ``self._thread``. Clearing it
        while the old thread is still inside ``run_once`` would let
        the next :meth:`start` clear the same stop event and spawn a
        SECOND daemon thread; the per-run lock blocks double-write
        but the duplicate poller is still a lifecycle bug.
        """
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if not thread.is_alive():
            self._thread = None
        else:
            logger.warning(
                "auto-report watcher (use_case_id=%s) did not exit "
                "within %.1fs; keeping the existing thread reference "
                "so a subsequent start() is a no-op rather than a "
                "duplicate-thread spawn",
                self._use_case_id,
                float(timeout) if timeout is not None else 0.0,
            )

    def _loop(self) -> None:
        """Daemon body: ``run_once`` on ``poll_interval`` until stopped."""
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                # Defence in depth: run_once already absorbs per-sim
                # failures, so this only catches a watcher-side bug
                # (e.g. an unexpected exception in the eligibility
                # filter). Log + keep polling so a bug never wedges
                # the thread silently.
                logger.exception("auto-watcher: poll cycle raised")
            self._stop.wait(self._poll_interval)


# ---------------------------------------------------------------------------
# Per-lane watcher registry (used by the app factory)
# ---------------------------------------------------------------------------


_WATCHER_LOCK = threading.Lock()
#: One watcher per registered ``use_case_id``. The auto-report
#: production deployment starts one daemon thread per lane so a
#: completed run under any namespaced data directory is picked up,
#: not just the historical ``policy`` default.
_WATCHERS: Dict[str, AutoReportWatcher] = {}


def get_or_create_watcher(
    *,
    use_case_id: str = DEFAULT_USE_CASE_ID,
    data_dir: Optional[str] = None,
    generator_factory: Optional[GeneratorFactory] = None,
    poll_interval_seconds: float = 5.0,
) -> AutoReportWatcher:
    """Return the watcher singleton for ``use_case_id``, instantiating on first call.

    Per-lane singleton: repeated ``create_app()`` calls in the same
    test process do not stack up multiple watcher threads for a
    given lane. Tests that want a fresh instance call
    :func:`reset_watcher_for_testing` first.
    """
    with _WATCHER_LOCK:
        watcher = _WATCHERS.get(use_case_id)
        if watcher is None:
            watcher = AutoReportWatcher(
                use_case_id=use_case_id,
                data_dir=data_dir,
                generator_factory=generator_factory,
                poll_interval_seconds=poll_interval_seconds,
            )
            _WATCHERS[use_case_id] = watcher
        return watcher


def reset_watcher_for_testing() -> None:
    """Drop every per-lane singleton (stopping any live threads) so tests start clean."""
    with _WATCHER_LOCK:
        for watcher in list(_WATCHERS.values()):
            try:
                watcher.stop(timeout=1.0)
            except Exception:
                pass
        _WATCHERS.clear()


def start_if_enabled() -> Optional[AutoReportWatcher]:
    """Start the default-lane watcher iff the opt-in env is set.

    Kept for callers that want a single lane (the historical
    behaviour). The app factory uses :func:`start_watchers_for_all_lanes`
    instead so every registered lane is covered. Safe to call
    repeatedly: the singleton's ``start()`` is itself idempotent.
    """
    if not is_enabled():
        return None
    watcher = get_or_create_watcher()
    watcher.start()
    return watcher


def start_watchers_for_all_lanes() -> List[AutoReportWatcher]:
    """Start one watcher per registered use case iff the env is set.

    Iterates :func:`app.simulation_platform.registry.list_use_cases`
    and installs a post-register observer so a lane registered AFTER
    this call also gets a watcher spawned automatically. The opt-in
    env is checked here so the daemon threads only spawn under
    ``AUTO_REPORT_GENERATE=true``. Calling this repeatedly is safe:
    the per-lane :func:`get_or_create_watcher` singleton + the
    observer's idempotent :func:`add_post_register_observer` make
    duplicate calls a no-op.
    """
    if not is_enabled():
        return []
    from . import registry as _registry
    started: List[AutoReportWatcher] = []
    ids = _registry.list_use_cases() or [DEFAULT_USE_CASE_ID]
    for use_case_id in ids:
        watcher = get_or_create_watcher(use_case_id=use_case_id)
        watcher.start()
        started.append(watcher)
    #: Install the post-register observer so a use_case registered
    #: AFTER boot also gets its watcher spawned. The observer's
    #: env re-check inside :func:`_on_use_case_registered` keeps
    #: the auto-report opt-in honest: if the operator unsets
    #: ``AUTO_REPORT_GENERATE`` after boot, the observer is a no-op
    #: rather than silently spawning a new daemon.
    _registry.add_post_register_observer(_on_use_case_registered)
    return started


def _on_use_case_registered(use_case_id: str) -> None:
    """Spawn a watcher for ``use_case_id`` iff the opt-in env is still set.

    Wired via :func:`registry.add_post_register_observer` from
    :func:`start_watchers_for_all_lanes`. Idempotent and safe to
    call from arbitrary threads (the per-lane singleton mutex
    inside :func:`get_or_create_watcher` serialises construction).
    """
    if not is_enabled():
        return
    try:
        watcher = get_or_create_watcher(use_case_id=use_case_id)
        watcher.start()
        logger.info(
            "auto-report watcher started for newly-registered lane "
            "(use_case_id=%s)", use_case_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "auto-report watcher could not be started for newly-"
            "registered lane (use_case_id=%s)", use_case_id,
        )


__all__ = [
    "AutoReportWatcher",
    "DEFAULT_USE_CASE_ID",
    "GenerationOutcome",
    "GeneratorFactory",
    "get_or_create_watcher",
    "is_enabled",
    "start_watchers_for_all_lanes",
    "production_generator_factory",
    "reset_watcher_for_testing",
    "start_if_enabled",
]
