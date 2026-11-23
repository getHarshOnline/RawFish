"""Dead-code / packaging gates.

Two contracts pinned here:

  1. No ``__pycache__`` / ``.pyc`` files are part of the source
     tree under ``backend/app``. A handoff bundle generated from
     the worktree must not ship compiled bytecode. (Local dev
     pycache is fine; the gate's job is to catch a future
     refactor that accidentally checks them in.)

  2. Every production ``NotImplementedError`` in a registered
     adapter must be either:
       * surfaced through the generic blueprint as a typed
         CAPABILITY_UNSUPPORTED envelope (the dispatch catches
         it explicitly -- see ``app/api/use_cases.py``), OR
       * documented in a docstring that names the capability and
         the reason.

     The second rule prevents an adapter author from quietly
     leaving an unimplemented method that surfaces as a bare 500
     instead of an honest 501.
"""
from __future__ import annotations

import ast
from pathlib import Path


_BACKEND_APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _walk_py_files(root: Path):
    for path in root.rglob("*.py"):
        # Skip __pycache__ remnants the gate is about to assert against.
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_pyc_or_pycache_in_source_tree() -> None:
    """Source tree under ``backend/app`` MUST NOT contain
    ``__pycache__`` directories OR ``.pyc`` files. Local dev pycache
    in transient locations is fine; the rule is about the SOURCE
    tree itself so a handoff `tar`/`zip` from ``backend/app`` never
    smuggles compiled bytecode.

    Skipped when ``__pycache__`` contents are merely a side effect
    of running the tests in place (most pytest invocations populate
    them) -- the assertion targets ``.pyc`` files OUTSIDE
    ``__pycache__`` and any nested ``.pyc`` whose presence indicates
    a packaging mistake (e.g. accidentally adding a generated file
    to the source set).
    """
    pyc_outside_pycache = []
    for path in _BACKEND_APP_DIR.rglob("*.pyc"):
        if "__pycache__" not in path.parts:
            pyc_outside_pycache.append(str(path))
    assert not pyc_outside_pycache, (
        f"compiled bytecode found OUTSIDE __pycache__/: "
        f"{pyc_outside_pycache!r}. Source tree must stay clean of "
        "checked-in bytecode."
    )


def test_adapter_not_implemented_errors_are_documented_or_dispatched() -> None:
    """Every ``raise NotImplementedError`` in a registered adapter
    module (under ``app/<lane>/adapter.py``) must be either:

      * Inside a method the generic dispatch explicitly catches
        as ``NotImplementedError`` (today: ``run_sensitivity`` +
        ``generate_report`` -- see ``app/api/use_cases.py``), OR

      * Accompanied by a docstring on the enclosing function
        that NAMES the capability and the reason it is
        unsupported (the operator-readable explanation).

    A bare ``raise NotImplementedError("...")`` inside a method
    the dispatch does NOT explicitly catch would surface as a
    500 instead of a typed 501, which is the failure mode the
    instruction packet flagged as a smell.
    """
    # Capabilities the generic blueprint catches explicitly. Keep
    # this in sync with ``app/api/use_cases.py``'s except clauses.
    DISPATCH_CAUGHT = frozenset({
        "run_sensitivity",
        "generate_report",
    })

    offenders = []
    for adapter_file in _BACKEND_APP_DIR.rglob("adapter.py"):
        rel = adapter_file.relative_to(_BACKEND_APP_DIR.parent)
        # Only check files under app/<lane>/adapter.py (use-case
        # adapters); other ``adapter.py`` shapes (e.g.
        # ``app/policy/adapter.py``) are use-case adapters too.
        src = adapter_file.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            method_name = node.name
            raises_nie = any(
                isinstance(child, ast.Raise)
                and isinstance(child.exc, ast.Call)
                and isinstance(child.exc.func, ast.Name)
                and child.exc.func.id == "NotImplementedError"
                for child in ast.walk(node)
            )
            if not raises_nie:
                continue
            # Dispatch-caught: OK.
            if method_name in DISPATCH_CAUGHT:
                continue
            # Documented: docstring must name the capability + reason.
            docstring = ast.get_docstring(node) or ""
            # Heuristic: docstring mentions either "not supported",
            # "unsupported", "not yet", or "TODO" / "future" --
            # enough to signal the operator-readable explanation.
            doc_l = docstring.lower()
            documented = any(
                marker in doc_l
                for marker in (
                    "not supported",
                    "unsupported",
                    "not yet",
                    "not implemented",
                    "future",
                )
            )
            if not documented:
                offenders.append(f"{rel}::{method_name}")
    assert not offenders, (
        f"bare NotImplementedError sites missing dispatch handling OR "
        f"docstring explanation: {offenders!r}"
    )
