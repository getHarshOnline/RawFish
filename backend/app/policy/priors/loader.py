"""Pure parser for audience-segment prior bundles (YAML or JSON).

The on-the-wire shape is :class:`AudienceSegmentPriors` (see
``app.policy.schema``). Both YAML and JSON paths parse into the SAME
canonical model -- the format is a serialisation choice, not a
semantic one. Parity contract: any JSON document and its equivalent
YAML document yield byte-identical ``model_dump()`` output.

YAML is parsed with ``yaml.safe_load`` only -- tags / anchors from
untrusted input are never executed. JSON is parsed with the stdlib
``json.loads``. Both syntax-error classes surface as
:class:`PriorValidationError` with a ``loc=["<root>"]`` and a
discriminator code (``yaml_syntax`` or ``json_syntax``) so the API
layer can map both to the same 400 envelope without leaking parser
internals.

Validation errors from the underlying :class:`AudienceSegmentPriors`
model surface as :class:`PriorValidationError` with one entry per
Pydantic error, ``loc`` flattened JSON-pointer-style (``priors[2].weight``)
and ``code`` set to the Pydantic error ``type`` (e.g.
``value_error``, ``string_pattern_mismatch``, ``literal_error``,
``missing``). The loader does NOT add any cross-bundle checks --
internal consistency is enforced by the model
(``_weights_sum_to_one_per_dimension``, the ``provenance_id`` pattern,
the ``support`` / ``stance`` Literals, the ``params`` ``min_length=1``).

The loader rejects:
  * YAML / JSON syntax errors at the parser layer
  * top-level non-mapping documents (a YAML list / scalar)
  * any field violation already enforced by :class:`AudienceSegmentPriors`
    -- weight sums per dimension, unknown support / stance, empty
    ``params``, malformed ``provenance_id`` patterns, unknown top-level
    keys (``extra='forbid'``)

This module is PURE: no clock, env, fs, random, network, LLM, or
Flask imports. YAML import is local inside :func:`parse_priors` so
the optional dep cost stays off the cold import path.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Mapping, Sequence

from pydantic import ValidationError

from ..schema import AudienceSegmentPriors


__all__ = ["PriorValidationError", "format_loc", "parse_priors"]


def format_loc(loc: tuple) -> str:
    """Turn a Pydantic ``loc`` tuple into a JSON-ish field path.

    ``("priors", 2, "weight")`` -> ``"priors[2].weight"``. An empty
    tuple resolves to ``"<root>"`` so syntax / top-level errors carry
    a non-empty pointer string the API layer can render uniformly.
    """
    parts: List[str] = []
    for piece in loc:
        if isinstance(piece, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{piece}]"
            else:
                parts.append(f"[{piece}]")
        else:
            parts.append(str(piece))
    return ".".join(parts) if parts else "<root>"


class PriorValidationError(ValueError):
    """Raised when a priors document fails parsing or model validation.

    Attributes
    ----------
    errors:
        Flat list of ``{"loc": str, "msg": str, "code": str}`` dicts.
        ``loc`` is the flattened JSON-pointer-style path from
        :func:`format_loc`; ``code`` is either the Pydantic
        ``error.type`` (e.g. ``string_pattern_mismatch``) or a loader
        discriminator (``yaml_syntax``, ``json_syntax``,
        ``not_mapping``).
    raw_errors:
        The original Pydantic ``ValidationError.errors()`` list when
        validation failed; an empty list for the syntax / not-mapping
        cases. Preserved verbatim for callers that need the full
        triplet.
    """

    def __init__(
        self,
        message: str,
        errors: List[Dict[str, str]],
        raw_errors: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(message)
        self.errors = errors
        self.raw_errors = list(raw_errors)


def _parse_text(text: str, fmt: Literal["yaml", "json"]) -> Any:
    """Dispatch text -> Python object on ``fmt``.

    YAML uses ``yaml.safe_load`` so anchors / tags from untrusted
    input are NOT executed. JSON uses the stdlib ``json.loads``.
    Both syntax-error classes surface as a :class:`PriorValidationError`
    with ``loc=["<root>"]`` and a ``code`` discriminator.
    """
    if fmt == "yaml":
        try:
            import yaml  # local import keeps optional dep cost off the cold path
        except ImportError as ie:  # pragma: no cover -- env hygiene
            raise PriorValidationError(
                "PyYAML is required to parse YAML priors documents",
                errors=[{"loc": "<root>", "msg": str(ie), "code": "yaml_import"}],
                raw_errors=[],
            ) from ie
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as ye:
            raise PriorValidationError(
                f"YAML syntax error: {ye}",
                errors=[{"loc": "<root>", "msg": str(ye), "code": "yaml_syntax"}],
                raw_errors=[],
            ) from ye
    elif fmt == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as je:
            raise PriorValidationError(
                f"JSON syntax error: {je}",
                errors=[{"loc": "<root>", "msg": str(je), "code": "json_syntax"}],
                raw_errors=[],
            ) from je
    else:  # pragma: no cover -- guarded by the Literal at the caller
        raise PriorValidationError(
            f"Unsupported fmt={fmt!r}; expected 'yaml' or 'json'",
            errors=[{"loc": "<root>", "msg": "unsupported_fmt", "code": "unsupported_fmt"}],
            raw_errors=[],
        )


def parse_priors(text: str, fmt: Literal["yaml", "json"]) -> AudienceSegmentPriors:
    """Parse a priors document text into a strict :class:`AudienceSegmentPriors`.

    Parameters
    ----------
    text:
        The raw document text (UTF-8 string).
    fmt:
        ``"yaml"`` (parsed via ``yaml.safe_load``) or ``"json"`` (parsed
        via ``json.loads``).

    Returns
    -------
    AudienceSegmentPriors:
        A validated model instance. ``model_dump()`` is parity-paired
        across the two formats -- the same canonical input yields
        identical output regardless of ``fmt``.

    Raises
    ------
    PriorValidationError:
        On YAML / JSON syntax errors, top-level non-mapping documents,
        or any underlying :class:`AudienceSegmentPriors` validation
        failure (weight-sum invariant, unknown support / stance,
        empty ``params``, malformed ``provenance_id``, unknown
        top-level keys via ``extra='forbid'``).
    """
    data = _parse_text(text, fmt)
    if not isinstance(data, dict):
        type_name = type(data).__name__
        raise PriorValidationError(
            f"Top-level document must be a mapping; got {type_name}",
            errors=[
                {
                    "loc": "<root>",
                    "msg": f"expected mapping, got {type_name}",
                    "code": "not_mapping",
                }
            ],
            raw_errors=[],
        )
    try:
        return AudienceSegmentPriors.model_validate(data)
    except ValidationError as ve:
        raw = ve.errors()
        flat = [
            {
                "loc": format_loc(tuple(e["loc"])),
                "msg": str(e["msg"]),
                "code": str(e["type"]),
            }
            for e in raw
        ]
        message = (
            f"Priors validation failed with {len(flat)} error(s):\n  - "
            + "\n  - ".join(f"{r['loc']}: {r['msg']}" for r in flat)
        )
        raise PriorValidationError(message, errors=flat, raw_errors=raw) from ve
