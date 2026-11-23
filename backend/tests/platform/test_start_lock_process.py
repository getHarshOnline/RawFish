from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path
from typing import Any, cast


def _lock_worker(run_dir: str, simulation_id: str, hold_seconds: float, queue: Any) -> None:
    from app.simulation_platform.run_locks import StartLockBusy, start_lock

    try:
        with start_lock(run_dir, simulation_id):
            queue.put("acquired")
            time.sleep(hold_seconds)
    except StartLockBusy:
        queue.put("busy")


def test_start_lock_excludes_second_process(tmp_path: Path) -> None:
    ctx_name = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
    ctx = cast(Any, mp.get_context(ctx_name))
    queue = ctx.Queue()
    run_dir = str(tmp_path / "run_process_lock")

    first = ctx.Process(target=_lock_worker, args=(run_dir, "sim-process", 0.5, queue))
    first.start()
    try:
        assert queue.get(timeout=2) == "acquired"

        second = ctx.Process(target=_lock_worker, args=(run_dir, "sim-process", 0.0, queue))
        second.start()
        try:
            assert queue.get(timeout=2) == "busy"
        finally:
            second.join(timeout=2)
            if second.is_alive():
                second.terminate()
                second.join(timeout=2)

        first.join(timeout=2)
        assert first.exitcode == 0
        assert second.exitcode == 0
    finally:
        if first.is_alive():
            first.terminate()
            first.join(timeout=2)
