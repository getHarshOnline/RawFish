"""Tests for :mod:`app.policy.sensitivity.aggregation` and the
``ensemble_n`` aggregation block on :class:`SensitivityReport`.

Two layers:

* Pure-function tests for :func:`iqm` and :func:`bootstrap_ci`. These
  pin the math (a known small sample, a controlled bootstrap seed)
  rather than just asserting "returns a float".
* Runner-side tests that drive :func:`run_oat` with ``ensemble_n`` set
  to confirm the optional :class:`FactorAggregation` block is populated
  when (and only when) the OAT sweep was replayed across multiple
  ensemble seeds.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pytest

from app.policy.sensitivity import (
    FactorAggregation,
    bootstrap_ci,
    iqm,
    run_oat,
)


class _StubScenarioForAgg:
    """Duck-typed scenario sufficient for ``_scenario_fingerprint``."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def model_dump_json(self) -> str:
        return json.dumps({"stub": self._payload}, sort_keys=True)


# ----------------------------------------------------------------------
# Pure iqm / bootstrap_ci tests
# ----------------------------------------------------------------------


def test_iqm_single_value_degenerates_to_that_value() -> None:
    """Sample of size 1: trim is 0 so the IQM is the single value."""
    assert iqm([0.42]) == pytest.approx(0.42)


def test_iqm_two_values_returns_arithmetic_mean_not_nan() -> None:
    """Regression: the previous quantile-mask implementation returned
    ``nan`` for ``iqm([1, 2])`` because the linear-interpolated
    ``[Q1, Q3] = [1.25, 1.75]`` band excluded both samples. The trim
    semantics (matching :func:`scipy.stats.trim_mean(arr, 0.25)`)
    give ``trim = floor(0.5) = 0`` so the IQM degenerates to the
    arithmetic mean -- a finite, well-defined value.

    Production matters: :class:`FactorAggregation` permits ``n >= 2``
    so a 2-seed ensemble MUST not produce a NaN aggregate; the
    downstream report renderer + the bootstrap CI both consume the
    IQM as a finite float."""
    assert iqm([1.0, 2.0]) == pytest.approx(1.5)
    assert not np.isnan(iqm([1.0, 2.0]))


def test_iqm_three_values_returns_arithmetic_mean() -> None:
    """``n == 3``: trim is 0 (``floor(0.75) == 0``); IQM is the plain
    arithmetic mean. The middle value is the median; the surrounding
    pair carries the rest of the mass."""
    assert iqm([1.0, 2.0, 9.0]) == pytest.approx(4.0)


def test_iqm_four_values_trims_one_per_side() -> None:
    """``n == 4``: trim 1 from each end; IQM is the mean of the two
    middle values. This is the first sample size at which trimming
    actually engages -- below this, IQM degenerates to the arithmetic
    mean."""
    # sorted [1, 2, 3, 100] -> trim 1 -> middle [2, 3] -> mean 2.5
    assert iqm([100.0, 1.0, 2.0, 3.0]) == pytest.approx(2.5)


def test_iqm_handles_constant_sample() -> None:
    """A constant sample (no spread): IQM equals that constant for
    every ``n``. Sanity check that the trim path doesn't divide by
    zero or accumulate float error on degenerate inputs."""
    for n in (1, 2, 3, 4, 5, 10):
        assert iqm([7.0] * n) == pytest.approx(7.0)


def test_iqm_drops_outliers_relative_to_arithmetic_mean() -> None:
    """A 1000x outlier moves the mean but not the IQM (rliable contract).

    With sample ``[1, 1, 1, 1, 1000]`` the arithmetic mean is 200.8 but
    the IQM is 1.0 -- the whole point of using IQM for small-N
    aggregation rather than mean + std.
    """
    sample = [1.0, 1.0, 1.0, 1.0, 1000.0]
    assert iqm(sample) == pytest.approx(1.0)
    # Sanity: the arithmetic mean is the value we are intentionally
    # NOT computing.
    assert sum(sample) / len(sample) == pytest.approx(200.8)


def test_iqm_known_small_sample_matches_hand_computation() -> None:
    """Hand-computed reference: symmetric-trim IQM on ``[1..9]``.

    For ``n == 9`` the trim is ``floor(0.25 * 9) == 2`` per tail; the
    surviving middle is ``[3, 4, 5, 6, 7]`` whose mean is ``5.0``.
    """
    assert iqm([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]) == pytest.approx(5.0)


def test_iqm_rejects_empty_sample() -> None:
    """Aggregating zero samples is a programmer error -- raise."""
    with pytest.raises(ValueError, match="empty sample"):
        iqm([])


def test_iqm_rejects_multidim_sample() -> None:
    """The aggregation primitives are 1-D; nd input is a programmer error."""
    with pytest.raises(ValueError, match="1-D sample"):
        iqm(np.array([[1.0, 2.0], [3.0, 4.0]]))  # type: ignore[arg-type]


def test_bootstrap_ci_band_contains_iqm_for_well_behaved_sample() -> None:
    """The bootstrap CI should bracket the point estimate for a sane sample."""
    sample = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    point = iqm(sample)
    rng = np.random.default_rng(123)
    low, high = bootstrap_ci(sample, n=2000, alpha=0.05, rng=rng)
    assert low <= point <= high


def test_bootstrap_ci_is_deterministic_for_fixed_rng() -> None:
    """Same Generator seed -> byte-identical CI bounds."""
    sample = [0.1, 0.2, 0.3, 0.4, 0.5]
    a_low, a_high = bootstrap_ci(
        sample, n=500, alpha=0.05, rng=np.random.default_rng(7),
    )
    b_low, b_high = bootstrap_ci(
        sample, n=500, alpha=0.05, rng=np.random.default_rng(7),
    )
    assert a_low == b_low
    assert a_high == b_high


def test_bootstrap_ci_returns_non_degenerate_band_for_varied_sample() -> None:
    """A sample with real spread yields a non-degenerate band.

    We don't pin an exact width (resample noise makes that brittle), but
    we DO pin the directional contract: the band is non-degenerate
    (low < high) and contains the point estimate.
    """
    sample = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    point = iqm(sample)
    low, high = bootstrap_ci(
        sample, n=2000, alpha=0.10, rng=np.random.default_rng(0),
    )
    assert low < high
    assert low <= point <= high


def test_bootstrap_ci_yields_finite_bounds_for_two_seed_ensemble() -> None:
    """Regression: ``ensemble_n == 2`` is the smallest production input
    (:class:`FactorAggregation` permits ``n >= 2``). Per-resample-row
    IQMs computed via the previous quantile-mask path returned
    ``nan`` whenever the row collapsed to a single distinct value
    (e.g. resample drew ``[1.0, 1.0]``), which propagated through
    :func:`numpy.quantile` and contaminated the CI bounds with
    ``nan``. The trim-based per-row IQM is finite for every row
    size ``>= 1``, so the CI bounds are always finite floats."""
    low, high = bootstrap_ci(
        [1.0, 2.0], n=500, alpha=0.05, rng=np.random.default_rng(0),
    )
    assert np.isfinite(low)
    assert np.isfinite(high)
    assert low <= high
    # The bootstrap CI of IQM([1, 2]) must bracket the point estimate
    # ``mean([1, 2]) == 1.5``; the CI of a 2-sample bootstrap is
    # always one of {1.0, 1.5, 2.0} per resample, so the band is
    # contained in ``[1.0, 2.0]``.
    assert 1.0 <= low <= 1.5 <= high <= 2.0


def test_bootstrap_ci_rejects_invalid_n() -> None:
    with pytest.raises(ValueError, match="n must be >= 1"):
        bootstrap_ci([0.1, 0.2], n=0)


def test_bootstrap_ci_rejects_invalid_alpha() -> None:
    with pytest.raises(ValueError, match=r"alpha must be in \(0, 1\)"):
        bootstrap_ci([0.1, 0.2], alpha=1.5)


# ----------------------------------------------------------------------
# Runner integration: aggregation block only when ensemble_n > 1
# ----------------------------------------------------------------------


def test_run_oat_ensemble_n_one_omits_aggregation_block() -> None:
    """Single-run path leaves :attr:`FactorSummary.aggregation` as ``None``.

    The aggregation block is meaningful only when the sweep was
    replayed across multiple seeds; the default ``ensemble_n=1`` path
    must NOT fabricate a degenerate one-value CI.
    """
    scenario = _StubScenarioForAgg("solo")
    report = run_oat(
        scenario,
        generated_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
        ensemble_n=1,
    )
    assert all(fs.aggregation is None for fs in report.factors)


def test_run_oat_ensemble_n_gt_one_populates_aggregation_block() -> None:
    """``ensemble_n >= 2`` populates an IQM + bootstrap CI per factor."""
    scenario = _StubScenarioForAgg("ensemble")
    report = run_oat(
        scenario,
        generated_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
        ensemble_n=5,
    )
    for fs in report.factors:
        agg = fs.aggregation
        assert isinstance(agg, FactorAggregation)
        assert agg.method == "iqm"
        assert agg.n == 5
        # The CI must bracket the point estimate (the percentile
        # bootstrap of the IQM contains the empirical IQM for any
        # non-degenerate alpha).
        assert agg.ci_low <= agg.iqm <= agg.ci_high


def test_run_oat_ensemble_n_aggregation_is_deterministic() -> None:
    """Same scenario + ensemble_n -> byte-identical aggregation block."""
    scenario = _StubScenarioForAgg("determinism")
    ts = datetime(2026, 6, 28, tzinfo=timezone.utc)
    a = run_oat(scenario, generated_at=ts, ensemble_n=4)
    b = run_oat(scenario, generated_at=ts, ensemble_n=4)
    assert a.model_dump_json() == b.model_dump_json()


def test_run_oat_rejects_invalid_ensemble_n() -> None:
    scenario = _StubScenarioForAgg("invalid")
    with pytest.raises(ValueError, match="ensemble_n must be >= 1"):
        run_oat(
            scenario,
            generated_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
            ensemble_n=0,
        )
