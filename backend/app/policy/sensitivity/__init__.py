"""Sensitivity-analysis subpackage.

Two pure deterministic modules:

* :mod:`oat_runner` -- one-at-a-time factor sweep producing a
  :class:`SensitivityReport`. Uses a deterministic surrogate kernel
  (``static_kernel_v1``) rather than re-running the simulation ensemble;
  the surrogate is documented on the kernel docstring and surfaced
  verbatim on the persisted artifact's ``kernel_disclaimer`` field so a
  reader cannot mistake the output for a real ensemble re-run.
* :mod:`driver_attribution` -- per-claim co-occurrence attribution
  (``cooccurrence_v1``) that reuses the existing
  :class:`app.policy.schema.DriverHint` model. Heuristic; the
  ``attribution_confidence`` field is pinned to ``"low"`` because the
  underlying OAT swings come from the surrogate kernel.

Neither module reads the clock, env, or filesystem. Neither imports
Flask, the LLM client, or any other I/O surface.
"""
from __future__ import annotations

from .aggregation import bootstrap_ci, iqm
from .driver_attribution import attribute_cooccurrence_v1
from .oat_runner import (
    GROUPED_FACTORS,
    SENSITIVITY_BOOTSTRAP_ITER_DEFAULT,
    SENSITIVITY_MAX_RUNS_DEFAULT,
    FactorAggregation,
    FactorSummary,
    OATRunEntry,
    SensitivityReport,
    run_oat,
)

__all__ = [
    "GROUPED_FACTORS",
    "SENSITIVITY_BOOTSTRAP_ITER_DEFAULT",
    "SENSITIVITY_MAX_RUNS_DEFAULT",
    "FactorAggregation",
    "FactorSummary",
    "OATRunEntry",
    "SensitivityReport",
    "attribute_cooccurrence_v1",
    "bootstrap_ci",
    "iqm",
    "run_oat",
]
