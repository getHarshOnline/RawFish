"""Pure aggregation primitives for ensemble sensitivity rollups.

When the OAT sweep is replayed across multiple ensemble seeds
(``ensemble_n > 1``) the per-factor swing becomes a *distribution* of
values rather than a single number. The rliable methodology argues that
the right summary for such small-N RL/sim ensembles is the
**interquartile mean (IQM)** with a **bootstrap confidence interval**
rather than the arithmetic mean plus standard deviation -- IQM is far
less sensitive to a single outlier seed than the mean, and bootstrap CIs
are honest about uncertainty in the small-N regime.

This module exposes two pure NumPy-only functions:

* :func:`iqm` -- interquartile mean over a 1-D sample. Drops the bottom
  and top quartiles via :func:`numpy.quantile` (linear interpolation,
  matching the rliable reference implementation's default) and returns
  the arithmetic mean of the surviving middle half.
* :func:`bootstrap_ci` -- nonparametric percentile bootstrap CI of the
  IQM. Resamples ``n`` times with replacement using
  :class:`numpy.random.Generator` (a caller-supplied
  :class:`numpy.random.Generator` seed keeps the function deterministic
  under test); returns the symmetric ``(alpha/2, 1 - alpha/2)``
  percentile band.

No scipy dependency: ``numpy.quantile`` covers the quartile cuts and
the bootstrap resample is a one-liner over
:meth:`numpy.random.Generator.choice`.

Pure module: no clock, no env, no filesystem, no global random. The
caller threads a :class:`numpy.random.Generator` (or omits it and gets
a fixed-seed default) so byte-identical inputs produce byte-identical
outputs across processes.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np
from numpy.typing import NDArray


# Fixed default seed for the bootstrap generator. The runner-side path
# threads its own Generator; this default is only here so the function
# is callable from a REPL without surprising non-determinism.
_DEFAULT_BOOTSTRAP_SEED: int = 0


def _coerce_1d(values: Sequence[float]) -> NDArray[np.float64]:
    """Coerce ``values`` into a 1-D ``float64`` NumPy array.

    Rejects empty input (an aggregate over zero samples has no defined
    IQM or CI -- raising early is friendlier than returning ``nan``).
    """
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError(
            f"aggregation: expected 1-D sample, got ndim={arr.ndim}"
        )
    if arr.size == 0:
        raise ValueError("aggregation: cannot aggregate an empty sample")
    return arr


def _iqm_from_sorted(sorted_arr: NDArray[np.float64]) -> float:
    """IQM of an already-sorted 1-D array.

    Implementation matches :func:`scipy.stats.trim_mean(arr, 0.25)`
    -- the function rliable's reference uses -- without taking a
    scipy dependency:

      * trim ``floor(0.25 * n)`` elements from EACH tail (so a
        symmetric chop, never a fractional one), then take the
        arithmetic mean of the surviving middle.
      * for ``n in (1, 2, 3)`` the trim is zero on each side so
        the IQM degenerates to the plain arithmetic mean -- this
        is the rliable / scipy behavior and is the principled
        answer in the small-N regime (a linear-interpolated [Q1,
        Q3] band can otherwise exclude every actual sample value,
        which would yield ``nan`` and corrupt downstream
        aggregation).

    Operates on a SORTED input so the bootstrap path can sort once
    per resample row and reuse the result. Callers that pass an
    unsorted array via :func:`iqm` get a sort in the wrapper.
    """
    n = sorted_arr.size
    trim = int(np.floor(0.25 * n))
    middle = sorted_arr[trim: n - trim] if trim else sorted_arr
    # ``middle`` is non-empty: for n=1..3 trim=0 so middle=sorted_arr;
    # for n>=4 trim<=n//4 < n/2 so middle has at least one element.
    return float(np.mean(middle))


def iqm(values: Sequence[float]) -> float:
    """Interquartile mean of ``values``.

    Returns the arithmetic mean of the *middle* 50 percent of the
    sample, computed via symmetric trimming
    (``floor(0.25 * n)`` elements from each tail) -- matching the
    :func:`scipy.stats.trim_mean(values, 0.25)` semantics that the
    rliable reference uses.

    Behaviour by sample size:

      * ``n == 1`` -- returns the single value (no trim possible).
      * ``n == 2`` -- returns ``mean(values)`` (trim is 0 because
        ``floor(0.5) == 0``); the previous
        :func:`numpy.quantile`-based mask returned ``nan`` here
        because the linear-interpolated ``[Q1, Q3]`` band excluded
        both samples.
      * ``n == 3`` -- returns ``mean(values)`` (trim 0); the median
        + the two siblings ARE the middle 50% when the sample is so
        small no further trimming is principled.
      * ``n >= 4`` -- trims one or more elements from each tail
        and returns the arithmetic mean of the rest. For ``n == 4``
        this is the mean of the middle two; for ``n == 8`` the mean
        of the middle four.

    Pure: no clock, no env, no I/O. Deterministic for a given input.
    """
    arr = _coerce_1d(values)
    sorted_arr = np.sort(arr)
    return _iqm_from_sorted(sorted_arr)


def bootstrap_ci(
    values: Sequence[float],
    *,
    n: int = 1000,
    alpha: float = 0.05,
    rng: Optional[np.random.Generator] = None,
) -> Tuple[float, float]:
    """Percentile-bootstrap CI of the IQM.

    Draws ``n`` resamples of size ``len(values)`` with replacement,
    computes the IQM of each resample, and returns the symmetric
    ``(alpha/2, 1 - alpha/2)`` percentile band of the resulting
    distribution.

    Args:
        values: the underlying 1-D sample to bootstrap over.
        n: number of bootstrap resamples (defaults to 1000, matching
            the rliable reference default for the small-N regime).
        alpha: total tail mass; the returned band covers
            ``1 - alpha`` of the resampled IQM distribution.
        rng: optional :class:`numpy.random.Generator`. Defaults to a
            fixed-seed generator so the function is deterministic when
            invoked without a caller-supplied source of randomness.

    Returns:
        ``(ci_low, ci_high)`` -- the lower and upper percentile cut
        points of the bootstrapped IQM distribution.
    """
    if n < 1:
        raise ValueError(f"bootstrap_ci: n must be >= 1, got {n!r}")
    if not (0.0 < alpha < 1.0):
        raise ValueError(
            f"bootstrap_ci: alpha must be in (0, 1), got {alpha!r}"
        )
    arr = _coerce_1d(values)
    generator = rng if rng is not None else np.random.default_rng(
        _DEFAULT_BOOTSTRAP_SEED
    )
    size = arr.size
    # Vectorized resample: shape (n, size) of indices into arr.
    idx = generator.integers(low=0, high=size, size=(n, size))
    samples = arr[idx]
    # Per-row IQM via symmetric trim (matches the :func:`iqm` semantics
    # above). Sorting each row once lets us slice the surviving middle
    # vectorised; the previous quantile-mask approach produced ``nan``
    # rows whenever a resample's interpolated ``[Q1, Q3]`` band landed
    # between actual array elements (small-N regime). The trim-based
    # path is finite-float-safe for every row size ``>= 1``.
    sorted_samples = np.sort(samples, axis=1)
    trim = int(np.floor(0.25 * size))
    if trim:
        middle = sorted_samples[:, trim: size - trim]
    else:
        middle = sorted_samples
    iqms = middle.mean(axis=1)
    ci_low = float(np.quantile(iqms, alpha / 2.0))
    ci_high = float(np.quantile(iqms, 1.0 - alpha / 2.0))
    return ci_low, ci_high


__all__ = ["iqm", "bootstrap_ci"]
