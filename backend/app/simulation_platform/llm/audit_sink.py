"""On-disk :class:`AuditSink` backed by ``run_metadata.json``.

:class:`RunMetadataAuditSink` appends an :class:`LLMAuditEntry` to the
``llm_audit`` field of the run's ``run_metadata.json`` via the canonical
:func:`app.simulation_platform.run_metadata.refresh_audit_entry` helper. Each
``append`` is atomic (the helper handles tempfile + ``os.replace``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from .audit_models import LLMAuditEntry


PathLike = Union[str, "os.PathLike[str]"]


class RunMetadataAuditSink:
    """Sink that appends each entry to ``<run_dir>/run_metadata.json``.

    Construction-time argument:
      * ``run_dir`` -- the directory containing the run's
        ``state.json`` + ``run_metadata.json``. If the metadata file
        does not yet exist (e.g. an early call site that fires before
        prepare), :func:`refresh_audit_entry` returns
        ``False`` and the entry is silently dropped -- this mirrors
        :func:`refresh_output_path`'s contract and keeps the sink safe
        to construct eagerly.
    """

    def __init__(self, run_dir: PathLike) -> None:
        self._run_dir = Path(os.fspath(run_dir))

    def append(self, entry: LLMAuditEntry) -> None:
        from app.simulation_platform.run_metadata import refresh_audit_entry

        refresh_audit_entry(self._run_dir, entry)


__all__ = ["RunMetadataAuditSink"]
