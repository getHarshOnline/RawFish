"""Tests for app.utils.atomic_io.

Pins four contracts: bytes/text/json round-trip; no partial file or
orphan ``.tmp`` on failure; concurrent same-path writers leave exactly
one input intact (no torn JSON); concurrent writers use distinct
tempfile names.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from app.utils.atomic_io import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)


# ---------------------------------------------------------------------------
# 1. Basic roundtrip
# ---------------------------------------------------------------------------


def test_atomic_write_basic_roundtrip(tmp_path: Path) -> None:
    """bytes / text / json each round-trip byte-identical for their layer
    and the JSON payload matches the deserialized object."""
    # bytes
    b_target = tmp_path / "blob.bin"
    payload = b"\x00\x01\x02hello\xff"
    atomic_write_bytes(b_target, payload)
    assert b_target.read_bytes() == payload

    # text (utf-8, includes non-ASCII to confirm encoding)
    t_target = tmp_path / "note.txt"
    atomic_write_text(t_target, "hello ☃ snowman")
    assert t_target.read_text(encoding="utf-8") == "hello ☃ snowman"

    # json: confirm deserialized value matches AND the on-disk byte format
    # uses the documented defaults (indent=2, sort_keys=True).
    j_target = tmp_path / "state.json"
    obj = {"z": 1, "a": [3, 2, 1], "nested": {"y": True, "x": None}}
    atomic_write_json(j_target, obj)
    on_disk = j_target.read_text(encoding="utf-8")
    assert json.loads(on_disk) == obj
    expected = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
    assert on_disk == expected, (
        "atomic_write_json must use indent=2, sort_keys=True, "
        "ensure_ascii=False so policy.py byte output is unchanged"
    )

    # No leftover tempfiles in the directory after success.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"unexpected tempfile orphans: {leftovers}"


# ---------------------------------------------------------------------------
# 2. Failure path: no partial file, no tempfile orphan
# ---------------------------------------------------------------------------


def test_atomic_write_no_partial_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``os.replace`` raises, the pre-existing target file is unchanged
    AND no ``.<name>.<random>.tmp`` orphan is left in the directory."""
    target = tmp_path / "state.json"
    initial = {"status": "created", "v": 1}
    atomic_write_json(target, initial)
    original_bytes = target.read_bytes()

    # Patch os.replace at the module the helper uses it from.
    import app.utils.atomic_io as atomic_io_mod

    def boom(_src: str, _dst: str) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_io_mod.os, "replace", boom)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_json(target, {"status": "starting", "v": 2})

    # Target unchanged.
    assert target.read_bytes() == original_bytes

    # No tempfile orphans.
    tmp_orphans = [
        p.name
        for p in tmp_path.iterdir()
        if p.name.endswith(".tmp") or ".tmp" in p.suffixes
    ]
    # Match the helper's prefix: hidden file starting with ".<name>."
    name_prefix = f".{target.name}."
    hidden_orphans = [
        p.name for p in tmp_path.iterdir() if p.name.startswith(name_prefix)
    ]
    assert tmp_orphans == [], f"orphan .tmp files left: {tmp_orphans}"
    assert hidden_orphans == [], (
        f"orphan hidden tempfiles left: {hidden_orphans}"
    )


# ---------------------------------------------------------------------------
# 3. Concurrent writers, same target
# ---------------------------------------------------------------------------


def test_atomic_write_concurrent_threads(tmp_path: Path) -> None:
    """16 threads write distinct JSON payloads to the SAME path. The final
    file must (a) parse as valid JSON and (b) equal exactly one of the
    inputs -- never an interleaved/torn mixture."""
    target = tmp_path / "shared.json"
    # Larger payloads make torn writes detectable; pad to push beyond
    # one filesystem block so partial writes (if any) would corrupt JSON.
    payloads = [
        {"writer": i, "padding": "x" * 4096, "items": list(range(i, i + 32))}
        for i in range(16)
    ]
    barrier = threading.Barrier(len(payloads))
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def writer(payload: dict) -> None:
        try:
            barrier.wait(timeout=5)
            atomic_write_json(target, payload)
        except BaseException as exc:  # capture, don't crash thread silently
            with errors_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(p,), name=f"w{p['writer']}")
        for p in payloads
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), f"writer {t.name} hung"

    assert errors == [], f"writer thread errors: {errors!r}"

    # Final file must parse cleanly -- this is the torn-write check.
    on_disk = target.read_text(encoding="utf-8")
    final = json.loads(on_disk)

    # Must equal exactly one of the input payloads.
    assert final in payloads, (
        f"final state {final!r} does not match any input payload; "
        "this indicates a torn / interleaved write"
    )

    # No tempfile orphans in the directory.
    leftovers = sorted(
        p.name for p in tmp_path.iterdir() if p.name != target.name
    )
    assert leftovers == [], f"orphan files after concurrent writers: {leftovers}"


# ---------------------------------------------------------------------------
# 4. Distinct tempfile names under contention
# ---------------------------------------------------------------------------


def test_atomic_write_unique_tmp_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch ``os.replace`` to sleep so two threads sit in the window
    between tempfile creation and rename simultaneously. The two threads
    MUST have produced distinct tempfile paths -- the old
    ``path.tmp.<pid>`` scheme would have collided here."""
    import app.utils.atomic_io as atomic_io_mod

    target = tmp_path / "race.json"
    observed_tmp_paths: list[str] = []
    paths_lock = threading.Lock()
    # Use a barrier so both threads enter os.replace at the same time.
    in_replace = threading.Barrier(2)
    real_replace = os.replace

    def slow_replace(src: str, dst: str) -> None:
        with paths_lock:
            observed_tmp_paths.append(src)
        # Wait until the other thread has also recorded its src path.
        in_replace.wait(timeout=5)
        # Brief sleep so both temp files genuinely coexist on disk.
        time.sleep(0.05)
        real_replace(src, dst)

    monkeypatch.setattr(atomic_io_mod.os, "replace", slow_replace)

    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def writer(i: int) -> None:
        try:
            atomic_write_json(target, {"writer": i})
        except BaseException as exc:
            with errors_lock:
                errors.append(exc)

    t1 = threading.Thread(target=writer, args=(1,), name="w1")
    t2 = threading.Thread(target=writer, args=(2,), name="w2")
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()

    assert errors == [], f"writer errors: {errors!r}"
    assert len(observed_tmp_paths) == 2, observed_tmp_paths
    assert observed_tmp_paths[0] != observed_tmp_paths[1], (
        "two concurrent writers reused the same tempfile path; this "
        "regresses to the broken path.tmp.<pid> behavior"
    )

    # Both tempfiles should have lived in the same directory as the target
    # (required for os.replace atomicity) and used the documented prefix.
    name_prefix = f".{target.name}."
    for p in observed_tmp_paths:
        assert os.path.dirname(p) == str(tmp_path), p
        assert os.path.basename(p).startswith(name_prefix), p
        assert p.endswith(".tmp"), p

    # Final file is valid JSON from exactly one of the writers.
    final = json.loads(target.read_text(encoding="utf-8"))
    assert final in ({"writer": 1}, {"writer": 2}), final


# ---------------------------------------------------------------------------
# 5. Migration guard: state.json / run_state.json writers go through atomic_io
# ---------------------------------------------------------------------------


def test_state_json_writers_use_atomic_io() -> None:
    """Lifecycle writers must not persist state via plain ``open(..., 'w')``.

    These files own the lifecycle-truth JSON the rest of the system reads;
    a torn write (partial truncate) surfaces as a JSONDecodeError on any
    concurrent reader. The canonical seam is ``app.utils.atomic_io``;
    this guard pins the migration so a future regression that
    reintroduces a plain write fails fast at test time instead of
    surfacing as a flaky read in production.
    """
    import re
    from pathlib import Path

    # Resolve repo-relative paths: tests/ -> backend/ -> app/
    repo_root = Path(__file__).resolve().parents[1]
    targets = [
        repo_root / "app" / "services" / "simulation_runner.py",
        repo_root / "app" / "api" / "use_cases.py",
    ]
    # Match both "w" and 'w' write-mode opens.
    pat = re.compile(r"""open\([^)]*['"]w['"]""")
    offenders: list[str] = []
    for path in targets:
        # The subprocess log-file handle in simulation_runner.py is the
        # one legitimate write-mode open (binary stdout/stderr pipe, not
        # JSON). It is allowlisted by name so the regression guard does
        # not chase a false positive.
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            if not pat.search(line):
                continue
            if "main_log_file" in line:
                continue
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        "state.json / run_state.json writes must go through "
        "app.utils.atomic_io.atomic_write_json; found plain "
        "open(..., 'w') sites:\n  " + "\n  ".join(offenders)
    )
