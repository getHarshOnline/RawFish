"""Atomic filesystem writes for state / metadata files.

Pattern: write to a uniquely-named tempfile in the SAME directory as the
target (required for ``os.replace`` to stay atomic on POSIX), ``flush``
+ ``fsync``, ``os.replace`` onto the target, then best-effort parent-dir
fsync (guarded for Windows). On any mid-write failure the tempfile is
``os.unlink``-ed so no orphan ``.<name>.<random>.tmp`` is left behind.

The ONLY sanctioned way to do small-file atomic writes in the backend;
do not reintroduce ``path.tmp.<pid>`` patterns elsewhere. ``target``
accepts ``Path | str``; internals normalize to ``str``.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Union

PathLike = Union[str, "os.PathLike[str]"]


def _fsync_parent_dir(parent: str) -> None:
    """Best-effort directory fsync. Silently no-ops on platforms (Windows)
    that do not support opening a directory for fsync."""
    try:
        dir_fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        # Windows: fsync on a directory handle raises; ignore.
        pass
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            pass


def atomic_write_bytes(target: PathLike, payload: bytes) -> None:
    """Atomically write ``payload`` to ``target``.

    On success, ``target`` contains the full payload or its previous
    contents -- never a partial / truncated file. On failure, no
    ``.<name>.<random>.tmp`` orphan is left behind.
    """
    target_path = os.fspath(target)
    parent = os.path.dirname(target_path) or "."
    os.makedirs(parent, exist_ok=True)

    target_name = os.path.basename(target_path)
    # NamedTemporaryFile with delete=False gives us a unique name AND an
    # already-open fd we can fsync before close. dir=parent guarantees
    # same-filesystem rename so os.replace is atomic.
    tmp = tempfile.NamedTemporaryFile(
        dir=parent,
        delete=False,
        prefix=f".{target_name}.",
        suffix=".tmp",
    )
    tmp_path = tmp.name
    try:
        try:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        finally:
            tmp.close()
        os.replace(tmp_path, target_path)
    except BaseException:
        # Any failure -- including KeyboardInterrupt -- must not leave the
        # tempfile orphaned. Best-effort unlink; ignore if already gone.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    _fsync_parent_dir(parent)


def atomic_write_text(
    target: PathLike,
    text: str,
    encoding: str = "utf-8",
) -> None:
    """Encode ``text`` and atomically write to ``target``."""
    atomic_write_bytes(target, text.encode(encoding))


def atomic_write_json(
    target: PathLike,
    obj: Any,
    *,
    indent: int = 2,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
) -> None:
    """Serialize ``obj`` with stable defaults and atomically write to
    ``target``.

    Defaults (``indent=2``, ``sort_keys=True``, ``ensure_ascii=False``)
    are the backend-wide convention for durable small JSON artifacts
    such as ``state.json`` and ``run_metadata.json``.
    """
    payload = json.dumps(
        obj,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=ensure_ascii,
    )
    atomic_write_text(target, payload)


__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
    "atomic_write_json",
]
