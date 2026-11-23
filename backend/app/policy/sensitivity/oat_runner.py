"""One-at-a-time (OAT) sensitivity sweep.

Produces a :class:`SensitivityReport` whose per-factor swings are
derived from a deterministic surrogate kernel (:func:`_static_kernel_v1`)
rather than real simulation ensemble re-runs. The surrogate is documented
on the kernel function docstring AND surfaced verbatim on every persisted
:class:`SensitivityReport` via the ``kernel`` + ``kernel_disclaimer``
fields so a downstream reader cannot mistake the output for a real
ensemble.

Pure module: no clock, no env, no filesystem, no network. The only
non-hash entropy source is a fixed-seed :class:`numpy.random.Generator`
threaded into the per-factor bootstrap-resample loop that produces the
``ci_low`` / ``ci_high`` fields on :class:`FactorSummary`. Byte-
identical scenario inputs + identical ``bootstrap_iter`` produce byte-
identical outputs because the generator seed is a module-level constant
(no wall clock, no process-global RNG state). ``datetime`` is imported
only for type annotations (``SensitivityReport.generated_at`` carries
the caller-supplied timestamp; the runner NEVER reads the wall clock).
``max_runs`` and ``bootstrap_iter`` must be supplied by the caller --
the route resolves the env overrides (``SENSITIVITY_MAX_RUNS`` /
``SENSITIVITY_BOOTSTRAP_ITER``) on the request boundary and passes the
resolved values in, so this module reads no env.

Factor families come from the canonical 7-row grouped-factor list
(see :data:`GROUPED_FACTORS`). Each factor is swept at one low and one
high perturbation magnitude (so 2 entries per factor). The default
``max_runs`` cap matches the architectural lock at 16.
"""
from __future__ import annotations

import hashlib
import struct
from datetime import datetime
from typing import Annotated, Any, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


# Canonical grouped-factor list (7 families). This is the wire contract
# for the OAT sweep; adding or renaming a factor is a schema-level
# breaking change visible at the boundary.
GROUPED_FACTORS: Tuple[str, ...] = (
    "segment_weights",
    "trust_priors",
    "sentiment_priors",
    "drift_rules.magnitude",
    "llm_temperature",
    "scenario_assumptions.platform",
    "persona_matrix.row_weights",
)


# Default max-runs cap. Architectural lock at 16 (one low + one high
# entry per factor across the 7-family list = 14, leaves headroom for
# 1-2 added factor families before truncation kicks in).
SENSITIVITY_MAX_RUNS_DEFAULT: int = 16


# Default bootstrap-iteration count for the per-factor confidence
# interval. 50 resamples is enough to surface the discrete percentile
# bands the static kernel can produce (with two deltas per factor, the
# achievable values are bounded by the four resample combinations
# {(low,low),(low,high),(high,low),(high,high)}); higher counts only
# tighten percentile estimation noise, not the underlying support. The
# route layer resolves the env override
# (:envvar:`SENSITIVITY_BOOTSTRAP_ITER`) on the request boundary and
# passes the resolved value in -- the runner reads no env.
SENSITIVITY_BOOTSTRAP_ITER_DEFAULT: int = 50


# Default perturbation magnitudes (signed deltas applied to the
# baseline). Symmetric low / high so the tornado-style ranking has a
# centered baseline regardless of factor family.
_DEFAULT_PERTURBATIONS: Tuple[float, float] = (-0.15, 0.15)


# Literal disclaimer string surfaced verbatim on every persisted
# SensitivityReport. Wording deliberately avoids the banned-token
# morphology enforced by language_guard.py (see that module for the
# canonical regex set).
_KERNEL_DISCLAIMER: str = (
    "deterministic surrogate; does not exercise the simulation ensemble. "
    "Beta upgrades to real ensemble re-runs."
)


def _scenario_fingerprint(scenario: Any) -> str:
    """Stable hex fingerprint of a scenario for kernel seeding.

    The fingerprint is sha256 over the scenario's deterministic JSON
    dump if the scenario quacks like a :class:`Scenario` (has
    ``model_dump_json``); otherwise falls back to ``repr(scenario)``
    sha256 (test stubs use this). Either way the result is a stable
    hex string so the kernel produces the SAME metric for the SAME
    scenario across processes.
    """
    if hasattr(scenario, "model_dump_json"):
        payload = scenario.model_dump_json().encode("utf-8")
    else:
        payload = repr(scenario).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _hash_to_unit_float(*parts: str) -> float:
    """Map a tuple of strings to a deterministic float in ``[0.0, 1.0]``.

    Concatenates with a record separator (``\\x1f``) so two parts that
    swap content do not produce the same digest. Takes the first 8 hex
    chars (32 bits) and rescales to ``[0.0, 1.0]``.
    """
    joined = "\x1f".join(parts).encode("utf-8")
    digest = hashlib.sha256(joined).digest()
    # First 4 bytes -> unsigned 32-bit -> rescale to [0, 1].
    (unsigned32,) = struct.unpack(">I", digest[:4])
    return unsigned32 / 0xFFFFFFFF


_BASELINE_METRIC_ANCHOR: float = 0.5


def _static_kernel_v1(
    scenario_fp: str, factor_id: str, perturbation: float,
) -> float:
    """Deterministic surrogate metric for an OAT perturbation.

    Method label: ``static_kernel_v1``. Confidence: ``low`` (always).

    This kernel is NOT a simulation re-run; it is a structural surrogate
    that satisfies the OAT contract:

      * Baseline metric is the canonical anchor (``0.5``) -- every
        factor's "no perturbation" point. Sharing the basis across
        factors is what makes ``delta = perturbed - baseline``
        meaningful as a per-factor sensitivity signal instead of
        noise dominated by the difference of two unrelated sha256s.
      * Each factor carries a deterministic ``sensitivity`` coefficient
        in ``[0.0, 1.0]`` derived from
        ``sha256(scenario_fp || factor_id)``; the perturbed metric
        is ``clamp(anchor + sensitivity * perturbation, 0, 1)``.
        Delta then equals ``sensitivity * perturbation`` modulo the
        clamp, so ranking by ``abs(delta)`` is ranking by surrogate
        sensitivity -- a structurally-honest signal even though the
        sensitivity itself is a hash-derived stand-in.
      * Same inputs always produce the same output -- byte-identical
        across processes (pure function over its three arguments).

    Surfaced on every :class:`SensitivityReport` via the ``kernel``
    discriminator field + the ``kernel_disclaimer`` literal so a
    reader cannot mistake the output for a real ensemble. Beta
    upgrades replace this with a real ensemble re-run path.
    """
    sensitivity = _hash_to_unit_float(scenario_fp, factor_id)
    perturbed = _BASELINE_METRIC_ANCHOR + sensitivity * perturbation
    return max(0.0, min(1.0, perturbed))


class OATRunEntry(BaseModel):
    """One row in the OAT perturbation sweep.

    A factor receives one entry per perturbation magnitude (default:
    one low + one high). The ``rank`` field is 0-indexed by
    ``abs(delta)`` descending (the tornado-diagram convention).
    """

    model_config = ConfigDict(extra="forbid")

    factor_id: Annotated[str, Field(min_length=1)]
    perturbation: float
    baseline_metric: Annotated[float, Field(ge=0.0, le=1.0)]
    perturbed_metric: Annotated[float, Field(ge=0.0, le=1.0)]
    delta: float
    rank: Annotated[int, Field(ge=0)]


class FactorAggregation(BaseModel):
    """Per-factor IQM + bootstrap CI summary when ``ensemble_n > 1``.

    Populated only when the OAT sweep was replayed across multiple
    ensemble seeds; left ``None`` on the single-run path (current
    default). The ``method`` discriminator is the literal ``"iqm"`` --
    per the rliable methodology, interquartile mean + percentile
    bootstrap CI is the honest summary for small-N ensembles where
    arithmetic mean + standard deviation is easily skewed by a single
    outlier seed.

    Fields:
        method: discriminator literal (``"iqm"``).
        iqm: interquartile mean of the per-seed swing magnitudes.
        ci_low / ci_high: bootstrap percentile-CI bounds of the IQM.
        n: number of ensemble seeds folded into the aggregate.
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["iqm"] = "iqm"
    iqm: float
    ci_low: float
    ci_high: float
    n: Annotated[int, Field(ge=2)]


class FactorSummary(BaseModel):
    """Aggregate of one factor's low / high swing.

    Per-factor block: low + high deltas (with their swing magnitudes),
    the combined ``abs_swing`` ranking input, and ``affected_claims``
    (always ``[]`` from the static kernel -- the surrogate does not
    have visibility into the claim set).

    ``aggregation`` is populated only when the OAT sweep was replayed
    across ``ensemble_n > 1`` seeds; on the current single-run path it
    is ``None`` (see :class:`FactorAggregation` for the rliable
    rationale).

    ``ci_low`` / ``ci_high`` are the per-factor bootstrap-percentile
    bounds on the swing magnitude that ``abs_swing`` reports as the
    point estimate -- the SAME quantity the tornado bar length depicts,
    so a renderer can overlay them as error bars directly. ``None`` on
    legacy artifacts.
    """

    model_config = ConfigDict(extra="forbid")

    factor: Annotated[str, Field(min_length=1)]
    delta_low: float
    delta_high: float
    swing_low: float
    swing_high: float
    abs_swing: float
    affected_claims: List[str] = Field(default_factory=list)
    aggregation: Optional[FactorAggregation] = None
    # Per-factor bootstrap-percentile bounds on the swing magnitude that
    # ``abs_swing`` reports as the point estimate. Computed by
    # resampling the factor's perturbation deltas with replacement
    # ``bootstrap_iter`` times (default 50; route may override via
    # :envvar:`SENSITIVITY_BOOTSTRAP_ITER`) and taking the symmetric
    # 2.5 % / 97.5 % percentile band of ``sum(abs(delta))`` across the
    # resamples. ``None`` on legacy artifacts that pre-date the
    # bootstrap field; the error-bar overlay on the tornado renders
    # only when both bounds are populated. The bound is over the SAME
    # quantity the tornado bar length depicts so a renderer can place
    # the whisker without an intermediate rescaling step.
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    # Per-factor attribution-method discriminator. Populated by
    # :func:`app.policy.sensitivity.driver_attribution.populate_affected_claims`:
    #
    #   * ``"structured_v1"`` -- ``affected_claims`` was filled via
    #     exact-match against ``Claim.driver_hints[*].ref``. High-
    #     confidence link: the claim itself declared the factor as a
    #     driver.
    #   * ``"substring_fallback_v1"`` -- the structured pass found no
    #     link; the fallback walked claim text + risk-tag tokens for a
    #     word-boundary match. Explicitly LOW confidence; clients
    #     surfacing per-factor attribution MUST visually distinguish
    #     this from the structured tier.
    #   * ``"no_match_v1"`` -- both passes ran and neither linked any
    #     claim to this factor. Distinguishes "populator ran but
    #     matched zero" from "legacy report -- populator never ran"
    #     (the latter leaves ``affected_claims_method`` at ``None``).
    #
    # ``None`` on legacy artifacts that pre-date the discriminator;
    # the read-side ALWAYS treats absence as "unknown method, do not
    # promote to high confidence in the UI".
    affected_claims_method: Optional[
        Literal["structured_v1", "substring_fallback_v1", "no_match_v1"]
    ] = None


class SensitivityReport(BaseModel):
    """Persisted OAT sensitivity report.

    Strict (``extra='forbid'``) so a hand-edited or otherwise corrupted
    artifact trips a ValidationError at read time. ``method`` +
    ``kernel`` are discriminator literals -- a future upgrade to a real
    ensemble re-run bumps the literal value so consumers comparing
    historical reports can detect the methodology change.

    ``kernel_disclaimer`` is the literal surfaced verbatim on the read
    path; the renderer / API consumer MUST display this text alongside
    any swing magnitude so a reader cannot mistake the surrogate output
    for a real ensemble.
    """

    model_config = ConfigDict(extra="forbid")

    simulation_id: Annotated[str, Field(min_length=1)]
    baseline_run_id: Annotated[str, Field(min_length=1)]
    method: Literal["one_at_a_time_v1"] = "one_at_a_time_v1"
    kernel: Literal["static_kernel_v1"] = "static_kernel_v1"
    kernel_disclaimer: str = _KERNEL_DISCLAIMER
    confidence: Literal["low", "medium", "high"] = "low"
    n_perturbations: Annotated[int, Field(ge=0)]
    capped: bool
    max_runs: Annotated[int, Field(ge=1)]
    runs: List[OATRunEntry]
    factors: List[FactorSummary]
    ranking: List[str] = Field(default_factory=list)
    generated_at: datetime
    # Explicit boolean marker set to True by
    # :func:`app.policy.sensitivity.driver_attribution.populate_affected_claims`
    # after it runs over a non-empty Report. Without this marker, the
    # consumer cannot distinguish "legacy report -- affected_claims
    # never populated; fall back to global ranking" from "populator
    # ran but matched zero claims for any factor; truthful empty
    # response is correct". Default False = legacy unpopulated.
    affected_claims_populated: bool = False


def _resolve_bootstrap_iter(arg: Optional[int]) -> int:
    """Resolve the per-call bootstrap-iteration count.

    Pure: only the explicit ``arg`` and the module-level
    :data:`SENSITIVITY_BOOTSTRAP_ITER_DEFAULT` constant feed the
    result. The route layer is responsible for reading the
    :envvar:`SENSITIVITY_BOOTSTRAP_ITER` override and passing the
    resolved value in -- this keeps the runner module
    clock/env/fs-free per the purity contract pinned in tests.
    """
    if arg is not None:
        if arg < 1:
            raise ValueError(
                f"bootstrap_iter must be >= 1, got {arg!r}"
            )
        return int(arg)
    return SENSITIVITY_BOOTSTRAP_ITER_DEFAULT


# Fixed seed for the per-factor bootstrap resampler. Kept module-local
# so byte-identical (scenario, bootstrap_iter) inputs produce byte-
# identical ``ci_low`` / ``ci_high`` outputs across processes -- the
# same determinism contract the rest of the runner carries.
_FACTOR_BOOTSTRAP_SEED: int = 0


def _factor_bootstrap_ci(
    deltas: Tuple[float, ...],
    *,
    bootstrap_iter: int,
    rng: Optional[Any] = None,
) -> Tuple[float, float]:
    """Per-factor bootstrap CI on the swing magnitude.

    Resamples the perturbation deltas with replacement
    ``bootstrap_iter`` times and returns the symmetric
    ``(2.5 %, 97.5 %)`` percentile band of ``sum(abs(d))`` across the
    resamples -- the SAME statistic the renderer reads as
    ``abs_swing``.

    Pure given a caller-supplied :class:`numpy.random.Generator`; when
    ``rng`` is ``None`` a fixed-seed generator is constructed so the
    helper stays deterministic in a REPL too. Empty input collapses to
    ``(0.0, 0.0)`` (no perturbations -> no swing -> no CI band) rather
    than raising; the runner only ever calls this with a non-empty
    tuple but the no-op behaviour keeps the helper friendly for
    targeted unit tests.
    """
    if bootstrap_iter < 1:
        raise ValueError(
            f"bootstrap_iter must be >= 1, got {bootstrap_iter!r}"
        )
    if not deltas:
        return (0.0, 0.0)
    import numpy as np

    arr = np.asarray(deltas, dtype=np.float64)
    abs_arr = np.abs(arr)
    generator = rng if rng is not None else np.random.default_rng(
        _FACTOR_BOOTSTRAP_SEED
    )
    size = abs_arr.size
    # Vectorized resample: shape (bootstrap_iter, size) of indices.
    idx = generator.integers(low=0, high=size, size=(bootstrap_iter, size))
    samples = abs_arr[idx]
    # Per-row sum of absolute deltas -- the same quantity the renderer
    # reads as ``abs_swing`` so the percentile band is directly
    # comparable to the bar length on the tornado.
    row_swings = samples.sum(axis=1)
    ci_low = float(np.quantile(row_swings, 0.025))
    ci_high = float(np.quantile(row_swings, 0.975))
    return ci_low, ci_high


def _resolve_max_runs(arg: Optional[int]) -> int:
    """Resolve the per-call max_runs cap.

    Pure: only the explicit ``arg`` and the module-level
    :data:`SENSITIVITY_MAX_RUNS_DEFAULT` constant feed the result.
    The route layer is responsible for reading the
    ``SENSITIVITY_MAX_RUNS`` env override and passing the resolved
    value in -- this keeps the runner module clock/env/fs-free per
    the purity contract pinned in tests.
    """
    if arg is not None:
        if arg < 1:
            raise ValueError(f"max_runs must be >= 1, got {arg!r}")
        return int(arg)
    return SENSITIVITY_MAX_RUNS_DEFAULT


def run_oat(
    scenario: Any,
    *,
    simulation_id: str = "sim_oat",
    baseline_run_id: str = "baseline",
    grouped_factors: Optional[Tuple[str, ...]] = None,
    max_runs: Optional[int] = None,
    generated_at: Optional[datetime] = None,
    ensemble_n: int = 1,
    bootstrap_iter: Optional[int] = None,
) -> SensitivityReport:
    """Run a one-at-a-time sweep over ``grouped_factors``.

    Deterministic: same scenario + same factor list + same max_runs
    always produces a byte-identical :class:`SensitivityReport`. The
    only optional non-determinism is ``generated_at`` which defaults to
    ``datetime.now(timezone.utc)`` but MAY be passed explicitly by the
    caller (the API route does so to keep the persisted artifact's
    timestamp aligned with the route's request-time clock read).

    The metric values come from :func:`_static_kernel_v1` -- a
    deterministic surrogate, NOT a real simulation ensemble re-run.
    See the kernel docstring for the surrogate contract.

    Args:
        scenario: Any object the kernel can fingerprint
            (:class:`Scenario` in production; duck-typed test stubs
            also work).
        simulation_id / baseline_run_id: opaque identifiers persisted
            on the report. The route handler passes the live values;
            unit tests use defaults.
        grouped_factors: tuple of factor names to sweep. Defaults to
            :data:`GROUPED_FACTORS`.
        max_runs: ceiling on the total number of OAT entries. Defaults
            via :func:`_resolve_max_runs`.
        generated_at: optional explicit timestamp for the report
            envelope; defaults to wall-clock UTC.
        ensemble_n: number of ensemble seeds the OAT sweep was replayed
            across. Defaults to ``1`` (the current single-run path: the
            surrogate kernel is deterministic so re-running it under
            the same scenario fingerprint yields byte-identical metrics,
            making the IQM aggregation redundant). When ``>= 2``, each
            :class:`FactorSummary` gets a populated
            :class:`FactorAggregation` block computed by
            :func:`app.policy.sensitivity.aggregation.iqm` +
            :func:`app.policy.sensitivity.aggregation.bootstrap_ci`
            over per-seed swing magnitudes -- the rliable methodology
            for honest small-N aggregation.
        bootstrap_iter: per-factor bootstrap resample count for the
            ``ci_low`` / ``ci_high`` fields. Defaults via
            :func:`_resolve_bootstrap_iter`
            (:data:`SENSITIVITY_BOOTSTRAP_ITER_DEFAULT`); the route
            layer resolves the :envvar:`SENSITIVITY_BOOTSTRAP_ITER`
            override on the request boundary and passes the resolved
            value in.

    Returns:
        A validated :class:`SensitivityReport`.
    """
    if ensemble_n < 1:
        raise ValueError(f"run_oat: ensemble_n must be >= 1, got {ensemble_n!r}")
    factors = grouped_factors if grouped_factors is not None else GROUPED_FACTORS
    cap = _resolve_max_runs(max_runs)
    bootstrap_count = _resolve_bootstrap_iter(bootstrap_iter)
    scenario_fp = _scenario_fingerprint(scenario)
    # Baseline metric shares the canonical anchor across every factor
    # (see _static_kernel_v1 docstring). Sharing the basis is what
    # makes delta = perturbed - baseline a meaningful per-factor signal
    # instead of the difference of two independent hashes.
    baseline_metric = _BASELINE_METRIC_ANCHOR

    # Build (factor_id, perturbation) sweep list in canonical order so
    # the cap-truncation behavior is deterministic.
    sweep: List[Tuple[str, float]] = []
    for factor in factors:
        for perturbation in _DEFAULT_PERTURBATIONS:
            sweep.append((factor, perturbation))
    capped = len(sweep) > cap
    sweep = sweep[:cap]

    # Compute (factor, perturbation, perturbed_metric, delta) for every
    # sweep entry.
    triples: List[Tuple[str, float, float, float]] = []
    for factor, perturbation in sweep:
        perturbed = _static_kernel_v1(scenario_fp, factor, perturbation)
        delta = perturbed - baseline_metric
        triples.append((factor, perturbation, perturbed, delta))

    # Rank by abs(delta) descending, breaking ties by (factor_id,
    # perturbation) lexical so the order is stable across processes.
    ranked = sorted(
        enumerate(triples),
        key=lambda item: (-abs(item[1][3]), item[1][0], item[1][1]),
    )
    rank_by_original_index: dict[int, int] = {}
    for rank, (orig_idx, _triple) in enumerate(ranked):
        rank_by_original_index[orig_idx] = rank

    # Build OATRunEntry list in rank order so the persisted shape lands
    # tornado-friendly out of the box.
    runs: List[OATRunEntry] = []
    for rank, (_orig_idx, (factor, perturbation, perturbed, delta)) in enumerate(
        ranked
    ):
        runs.append(
            OATRunEntry(
                factor_id=factor,
                perturbation=perturbation,
                baseline_metric=baseline_metric,
                perturbed_metric=perturbed,
                delta=delta,
                rank=rank,
            )
        )

    # Aggregate per-factor summaries (low + high). A factor with only
    # one entry under cap-truncation gets that entry's values mirrored
    # for the missing side so downstream renderers always see a
    # complete pair.
    per_factor: dict[str, dict[str, float]] = {}
    # Parallel collection of the raw delta list per factor (needed by
    # the bootstrap CI helper -- it resamples the deltas with
    # replacement to get the swing-magnitude percentile band).
    per_factor_deltas: dict[str, List[float]] = {}
    for entry in runs:
        bucket = per_factor.setdefault(entry.factor_id, {})
        side = "low" if entry.perturbation < 0 else "high"
        bucket[f"delta_{side}"] = entry.delta
        bucket[f"swing_{side}"] = abs(entry.delta)
        per_factor_deltas.setdefault(entry.factor_id, []).append(entry.delta)

    # One numpy Generator threaded through every per-factor bootstrap
    # call so the (factor-order, bootstrap_count) pair fully determines
    # the percentile-band output. Seed is module-level so two processes
    # given the same scenario produce byte-identical reports.
    import numpy as _np
    bootstrap_rng = _np.random.default_rng(_FACTOR_BOOTSTRAP_SEED)

    factor_summaries: List[FactorSummary] = []
    for factor in factors:
        bucket = per_factor.get(factor)
        if not bucket:
            continue
        delta_low = bucket.get("delta_low", 0.0)
        delta_high = bucket.get("delta_high", 0.0)
        swing_low = bucket.get("swing_low", 0.0)
        swing_high = bucket.get("swing_high", 0.0)
        abs_swing = swing_low + swing_high
        ci_low, ci_high = _factor_bootstrap_ci(
            tuple(per_factor_deltas.get(factor, ())),
            bootstrap_iter=bootstrap_count,
            rng=bootstrap_rng,
        )
        factor_summaries.append(
            FactorSummary(
                factor=factor,
                delta_low=delta_low,
                delta_high=delta_high,
                swing_low=swing_low,
                swing_high=swing_high,
                abs_swing=abs_swing,
                affected_claims=[],
                ci_low=ci_low,
                ci_high=ci_high,
            )
        )

    # Ensemble aggregation: when ``ensemble_n > 1`` the rliable
    # methodology asks for IQM + bootstrap CI rather than mean + std.
    # The static_kernel_v1 surrogate is deterministic in
    # (scenario_fp, factor_id, perturbation) so per-seed variation is
    # synthesized by salting the scenario fingerprint with a seed
    # suffix; this keeps the function pure (no global RNG read) while
    # giving the aggregation primitives a meaningful sample to summarize.
    # When ``ensemble_n == 1`` the aggregation block is omitted (a single
    # value has no meaningful CI; the existing delta_low / delta_high
    # already carry the full signal).
    if ensemble_n > 1:
        # Local import to keep the numpy dependency confined to the
        # path that actually needs it; the single-run path stays
        # numpy-free at import time.
        from .aggregation import bootstrap_ci, iqm

        # Fixed-seed Generator so the report is byte-identical across
        # processes for the same (scenario, ensemble_n) input -- the
        # purity contract carried by the rest of the runner.
        import numpy as np

        rng = np.random.default_rng(0)
        aggregated: List[FactorSummary] = []
        for fs in factor_summaries:
            per_seed_swings: List[float] = []
            for seed_idx in range(ensemble_n):
                seed_fp = f"{scenario_fp}:seed={seed_idx}"
                seed_swings: List[float] = []
                for perturbation in _DEFAULT_PERTURBATIONS:
                    perturbed = _static_kernel_v1(
                        seed_fp, fs.factor, perturbation,
                    )
                    seed_swings.append(abs(perturbed - baseline_metric))
                # One value per seed: the combined (low + high) swing,
                # matching the abs_swing summary used for ranking.
                per_seed_swings.append(sum(seed_swings))
            agg_iqm = iqm(per_seed_swings)
            ci_low, ci_high = bootstrap_ci(per_seed_swings, rng=rng)
            aggregated.append(
                fs.model_copy(update={
                    "aggregation": FactorAggregation(
                        iqm=agg_iqm,
                        ci_low=ci_low,
                        ci_high=ci_high,
                        n=ensemble_n,
                    ),
                })
            )
        factor_summaries = aggregated

    factor_summaries.sort(key=lambda fs: (-fs.abs_swing, fs.factor))
    ranking = [fs.factor for fs in factor_summaries]

    if generated_at is None:
        # Pure-module contract: this function does not read the wall
        # clock. The route layer reads ``datetime.now(timezone.utc)``
        # once at request entry and passes the value in; any caller
        # that omits it must do the same. Raising here (rather than
        # falling back to ``datetime.now``) is what keeps the runner
        # honestly pure and stops a stray sensitivity_id collision
        # caused by mismatched per-process timestamps.
        raise ValueError(
            "run_oat: generated_at is required (purity contract -- the "
            "runner does not read the wall clock; supply "
            "datetime.now(timezone.utc) at the call boundary)."
        )
    return SensitivityReport(
        simulation_id=simulation_id,
        baseline_run_id=baseline_run_id,
        n_perturbations=len(runs),
        capped=capped,
        max_runs=cap,
        runs=runs,
        factors=factor_summaries,
        ranking=ranking,
        generated_at=generated_at,
    )


__all__ = [
    "GROUPED_FACTORS",
    "SENSITIVITY_BOOTSTRAP_ITER_DEFAULT",
    "SENSITIVITY_MAX_RUNS_DEFAULT",
    "FactorAggregation",
    "FactorSummary",
    "OATRunEntry",
    "SensitivityReport",
    "run_oat",
]
