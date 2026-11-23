"""Priors loader package.

Pure parsing surface for audience-segment prior bundles. API callers use
``parse_priors`` to validate a YAML or JSON document into a strict
:class:`AudienceSegmentPriors`; on failure the
raised :class:`PriorValidationError` carries structured
``{"loc", "msg", "code"}`` triples that the API layer surfaces verbatim
under ``error.details.errors``.

This module is pure: no clock, env, fs, random, network, LLM, or Flask
imports. Atomic disk writes happen in the API layer, not here.
"""

from .loader import PriorValidationError, format_loc, parse_priors

__all__ = ["PriorValidationError", "format_loc", "parse_priors"]
