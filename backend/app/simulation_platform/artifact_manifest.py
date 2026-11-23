"""Prepare-time artifact manifest primitives.

The manifest is a small index a use case writes alongside its prepare
artifacts so the downstream start path can refuse runs whose prepare
set is incomplete and pin provenance (per-artifact sha256 + on-disk
byte counts) into ``run_metadata.json``.

Shape on disk::

    {
        "scenario_hash": "<hex>",
        "created_at_iso": "<iso8601>",
        "artifacts": [
            {"name": "<artifact>", "relative_path": "<artifact>",
             "sha256": "<hex>", "bytes": <int>},
            ...
        ]
    }

The ``artifacts`` array is sorted by ``name`` before being persisted so
two prepares of the same inputs produce byte-identical manifest bytes
(the JSON write itself goes through :func:`atomic_write_json` with the
canonical ``sort_keys=True`` default).

This module is intentionally use-case-agnostic: the artifact names are
INPUTS (``artifact_specs``) rather than hardcoded constants, so a
non-policy use case can compose the same primitives without forking
the file. Domain-specific concerns -- the prepare envelope, the
deterministic ``created_at_iso`` derivation, the per-artifact payload
builders -- stay in the calling use case's prepare writer.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Sequence, Tuple

from ..utils.atomic_io import atomic_write_json


# ---------------------------------------------------------------------------
# File primitives
# ---------------------------------------------------------------------------


def sha256_of_file(path: str) -> str:
    """Hex sha256 of ``path``'s on-disk bytes.

    Reads in 64 KiB chunks so large artifacts do not pin the whole
    payload in memory; the digest is byte-identical to a single-pass
    ``hashlib.sha256(open(path,"rb").read()).hexdigest()`` over the
    same file.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def bytes_on_disk(path: str) -> int:
    """Return the on-disk byte count for ``path`` via :func:`os.path.getsize`."""
    return os.path.getsize(path)


# ---------------------------------------------------------------------------
# Manifest write
# ---------------------------------------------------------------------------


def write_manifest(
    *,
    run_dir: str,
    scenario_hash: str,
    created_at_iso: str,
    artifact_specs: Sequence[Tuple[str, str]],
) -> Dict[str, Any]:
    """Compute + write ``manifest.json`` to ``run_dir``.

    ``artifact_specs`` is a sequence of ``(name, absolute_path)`` pairs;
    the artifact names are inputs (NOT a hardcoded policy list) so any
    use case can compose this primitive. The hashes are computed by
    reading the on-disk bytes back, so the manifest accurately reflects
    what a downstream consumer will read off disk.

    The ``artifacts`` array is sorted by ``name`` before being written
    so dict-iteration order in the caller can never affect the bytes;
    the dispatch through :func:`atomic_write_json` applies the canonical
    ``sort_keys=True`` JSON serialization on top.

    Returns the manifest dict that was written.
    """
    artifacts_entries: List[Dict[str, Any]] = []
    for name, path in artifact_specs:
        artifacts_entries.append({
            "name": name,
            "relative_path": name,
            "sha256": sha256_of_file(path),
            "bytes": bytes_on_disk(path),
        })
    artifacts_entries.sort(key=lambda e: e["name"])

    manifest: Dict[str, Any] = {
        "scenario_hash": scenario_hash,
        "created_at_iso": created_at_iso,
        "artifacts": artifacts_entries,
    }
    manifest_path = os.path.join(run_dir, "manifest.json")
    atomic_write_json(manifest_path, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Manifest read
# ---------------------------------------------------------------------------


def manifest_path_for(run_dir: str) -> str:
    """Return the conventional ``manifest.json`` path under ``run_dir``."""
    return os.path.join(run_dir, "manifest.json")


def read_manifest(run_dir: str) -> Dict[str, Any]:
    """Load and JSON-decode ``manifest.json`` from ``run_dir``.

    Raises :class:`FileNotFoundError` if the manifest is absent (callers
    that need a soft existence check should use :func:`manifest_path_for`
    + :func:`os.path.exists` first) and :class:`json.JSONDecodeError`
    if the on-disk bytes are not valid JSON -- both are surfaced rather
    than swallowed so corruption is operator-visible.
    """
    with open(manifest_path_for(run_dir), "r", encoding="utf-8") as fh:
        return json.load(fh)


def artifact_paths_from_manifest(manifest: Dict[str, Any]) -> Dict[str, str]:
    """Return a ``{name: relative_path}`` map from a manifest dict."""
    return {entry["name"]: entry["relative_path"] for entry in manifest["artifacts"]}


def artifact_hashes_from_manifest(manifest: Dict[str, Any]) -> Dict[str, str]:
    """Return a ``{name: sha256}`` map from a manifest dict."""
    return {entry["name"]: entry["sha256"] for entry in manifest["artifacts"]}


__all__ = [
    "sha256_of_file",
    "bytes_on_disk",
    "write_manifest",
    "manifest_path_for",
    "read_manifest",
    "artifact_paths_from_manifest",
    "artifact_hashes_from_manifest",
]
