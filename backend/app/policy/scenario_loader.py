"""Scenario loader.

Loads :class:`Scenario` instances from a YAML/JSON file (dispatched on
suffix) or an in-memory dict. Validation errors surface as
:class:`ScenarioValidationError` (a ``ValueError`` subclass) carrying a
flat list of ``"field.path: message"`` strings derived from
``pydantic.ValidationError.errors()`` so callers need not import
Pydantic. ``allow_expensive`` is resolved here via
:func:`app.utils.runtime_env.get_allow_expensive` and forwarded through
``ValidationInfo.context`` so the schema never touches the env itself.

Example::

    from app.policy.scenario_loader import load_scenario
    scen = load_scenario("examples/policy/creator/sample-thread.yaml")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from pydantic import ValidationError

from .schema import Scenario


def _resolve_allow_expensive(explicit: Optional[bool]) -> bool:
    """Resolve the ``allow_expensive`` flag for scale-cap context.

    Precedence: explicit caller value > ``POLICY_ALLOW_EXPENSIVE`` env. The
    schema itself NEVER reads the env -- callers resolve the flag here and
    pass it through ``ValidationInfo.context``.
    """
    if explicit is not None:
        return bool(explicit)
    # env parsing SSoT is app.utils.runtime_env.get_allow_expensive.
    from ..utils.runtime_env import get_allow_expensive
    return get_allow_expensive()


logger = logging.getLogger(__name__)


class ScenarioValidationError(ValueError):
    """Raised when scenario data fails Pydantic validation.

    Attributes
    ----------
    errors:
        Flat list of ``"field.path: message"`` strings derived from
        ``ValidationError.errors()``. Useful for surfacing structured
        validation diagnostics to the API / CLI without leaking Pydantic
        internals.
    raw_errors:
        The original list of dicts returned by ``ValidationError.errors()``,
        preserved for callers that need the full ``loc``/``type``/``ctx``
        triplet.
    """

    def __init__(
        self,
        message: str,
        errors: List[str],
        raw_errors: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(message)
        self.errors = errors
        self.raw_errors = raw_errors


def _format_loc(loc: tuple) -> str:
    """Turn a Pydantic ``loc`` tuple into a JSON-ish field path.

    ``("stakeholders", 2, "influence_level")`` -> ``"stakeholders[2].influence_level"``.
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


def _wrap_validation_error(err: ValidationError) -> ScenarioValidationError:
    raw = err.errors()
    formatted = [f"{_format_loc(e['loc'])}: {e['msg']}" for e in raw]
    message = f"Scenario validation failed with {len(formatted)} error(s):\n  - " + "\n  - ".join(
        formatted
    )
    return ScenarioValidationError(message, formatted, raw)


def load_scenario_dict(
    data: Dict[str, Any], *, allow_expensive: Optional[bool] = None
) -> Scenario:
    """Validate an in-memory dict and return a ``Scenario``.

    ``allow_expensive`` (or ``POLICY_ALLOW_EXPENSIVE=true`` env, used as
    fallback) relaxes the scale-cap from ``SCALE_CAP_POC`` to
    ``SCALE_CAP_LARGE``. Raises ``ScenarioValidationError`` on failure.
    """
    ctx = {"allow_expensive": _resolve_allow_expensive(allow_expensive)}
    try:
        return Scenario.model_validate(data, context=ctx)
    except ValidationError as e:
        raise _wrap_validation_error(e) from e


def load_scenario(
    path: Union[str, Path], *, allow_expensive: Optional[bool] = None
) -> Scenario:
    """Load a ``Scenario`` from a YAML (.yaml/.yml) or JSON (.json) file.

    Dispatch is by suffix (case-insensitive). YAML uses ``yaml.safe_load``
    so anchors/tags from untrusted input are not executed.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Scenario file not found: {p}")

    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # local import keeps optional dep cost off the hot path
        except ImportError as e:  # pragma: no cover - env hygiene
            raise RuntimeError(
                "PyYAML is required to load .yaml/.yml scenarios; "
                "install it via `uv add pyyaml` or pin in pyproject.toml."
            ) from e
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    elif suffix == ".json":
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        raise ValueError(
            f"Unsupported scenario file suffix {p.suffix!r}; "
            "expected .yaml, .yml, or .json"
        )

    if not isinstance(data, dict):
        raise ScenarioValidationError(
            f"Scenario file {p} must contain a top-level mapping; got {type(data).__name__}",
            errors=[f"<root>: expected mapping, got {type(data).__name__}"],
            raw_errors=[],
        )

    logger.debug("loaded scenario from %s (suffix=%s)", p, suffix)
    return load_scenario_dict(data, allow_expensive=allow_expensive)


# Common loader aliases used by tests and scripts that want explicit input
# names while sharing the same suffix-dispatching implementation.

def load_scenario_from_yaml(
    path: Union[str, Path], *, allow_expensive: Optional[bool] = None
) -> Scenario:
    """Alias for :func:`load_scenario` (YAML/JSON dispatched on suffix)."""
    return load_scenario(path, allow_expensive=allow_expensive)


def load_scenario_from_dict(
    data: Dict[str, Any], *, allow_expensive: Optional[bool] = None
) -> Scenario:
    """Alias for :func:`load_scenario_dict`."""
    return load_scenario_dict(data, allow_expensive=allow_expensive)
