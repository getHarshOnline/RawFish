"""``SimulationRunner._read_action_log`` must NOT lose data when the
writer is mid-flight.

The action log is a tail-style JSONL file written by the runner
subprocess and read by the monitor thread. Each tail pass seeks to the
saved byte ``position`` and reads forward. A naive ``for line in f``
loop that returns ``f.tell()`` swallows partial bytes (JSONDecodeError
caught and ignored) AND advances the offset past them; the now-complete
line that follows is never re-read and the record is permanently lost.

The loop tracks ``last_good_pos`` (advanced only when a line ends in
'\\n' AND was successfully parsed, OR when an empty/garbage complete
line was consumed) and returns that offset. A trailing partial line
leaves ``last_good_pos`` un-advanced so the next tail pass re-reads it
from the start.

These tests pin both the no-data-loss invariant and that already-
complete lines do still advance (no infinite re-read).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.simulation_runner import (
    SimulationRunState,
    SimulationRunner,
)


def _write_raw(path: Path, data: str, mode: str = "a") -> None:
    """Append raw bytes to ``path`` without forcing a trailing newline.

    Tests deliberately stage partial-line scenarios (no '\\n') so we
    bypass the higher-level ``write_jsonl`` helpers and write bytes
    directly.
    """
    with open(path, mode, encoding="utf-8") as fh:
        fh.write(data)


def _make_state(sim_id: str = "sim_test_action_log") -> SimulationRunState:
    """Minimal in-memory state suitable for the tailer's mutations."""
    return SimulationRunState(simulation_id=sim_id)


def test_partial_trailing_line_is_reparsed_after_writer_completes_it(
    tmp_path,
):
    """Core invariant: a complete line written AFTER a partial
    tail pass MUST be parsed on the next tail pass (no data loss).

    Scenario (per task spec):
      1. Writer emits one complete line ("round_end" event for round 1).
      2. Tailer reads -> sees the complete line, advances offset.
      3. Writer emits a partial line (no trailing '\\n' yet) for round 2.
      4. Tailer reads -> partial line is NOT consumed, offset stays put.
      5. Writer completes the partial line.
      6. Tailer reads -> the now-complete round-2 line IS parsed.
    """
    log_path = tmp_path / "actions.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: complete line for round 1.
    line1 = json.dumps({
        "event_type": "round_end",
        "round": 1,
        "simulated_hours": 0,
    })
    _write_raw(log_path, line1 + "\n")

    state = _make_state()

    # Step 2: first tail. Round counters advance for round 1.
    pos1 = SimulationRunner._read_action_log(
        str(log_path), 0, state, platform="twitter",
    )
    assert state.twitter_current_round == 1, (
        "round-1 line should have been parsed on first tail pass"
    )

    # Step 3: writer emits partial bytes (no newline) for round 2.
    partial = json.dumps({
        "event_type": "round_end",
        "round": 2,
        "simulated_hours": 1,
    })
    # Deliberately truncate to simulate mid-write (omit closing brace).
    truncated = partial[: len(partial) // 2]
    _write_raw(log_path, truncated)

    # Step 4: tail sees the partial line. It must NOT advance state OR
    # the saved offset past the partial bytes.
    pos2 = SimulationRunner._read_action_log(
        str(log_path), pos1, state, platform="twitter",
    )
    assert state.twitter_current_round == 1, (
        "partial round-2 line must NOT be parsed (no data, no mutation)"
    )
    assert pos2 == pos1, (
        f"tail must NOT advance past partial bytes; pos1={pos1} pos2={pos2}"
    )

    # Step 5: writer completes the round-2 line. We rewrite the file so
    # the second half + newline land where the truncated half currently
    # sits -- this matches how a real append-mode writer would flush the
    # rest of the buffer once the I/O completes.
    # The partial bytes already on disk are the FIRST half of ``partial``;
    # we now append the SECOND half + '\n' to produce the full record.
    rest = partial[len(partial) // 2:]
    _write_raw(log_path, rest + "\n")

    # Step 6: tail re-reads from the saved offset. The completed line
    # MUST now be parsed -- this is the no-data-loss invariant.
    pos3 = SimulationRunner._read_action_log(
        str(log_path), pos2, state, platform="twitter",
    )
    assert state.twitter_current_round == 2, (
        "round-2 line was not re-parsed after the writer completed it -- "
        "data loss"
    )
    assert pos3 > pos2, "offset must advance after the completed line"


def test_garbage_complete_line_does_not_block_tailer(tmp_path):
    """A malformed but newline-terminated line is COMPLETE; the tailer
    must skip it (logging-only) and continue past it -- never infinite-
    loop on a syntactically broken record.
    """
    log_path = tmp_path / "actions.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Garbage complete line (ends in '\n' but is not valid JSON).
    _write_raw(log_path, "this is not json at all\n")
    # Followed by a real complete line we want to be sure still gets
    # consumed -- if the tailer infinite-loops on the garbage line the
    # round-1 mutation will never happen.
    real = json.dumps({
        "event_type": "round_end",
        "round": 1,
        "simulated_hours": 0,
    })
    _write_raw(log_path, real + "\n")

    state = _make_state()
    pos = SimulationRunner._read_action_log(
        str(log_path), 0, state, platform="reddit",
    )
    assert state.reddit_current_round == 1, (
        "tail must skip the garbage line and parse the following real line"
    )
    # Offset must be at end of file (both lines consumed).
    assert pos == log_path.stat().st_size


def test_complete_line_advances_offset(tmp_path):
    """A pure-success path: one complete line, one tail pass; the
    saved offset moves to the end of the line.

    This pins the non-regression: the partial-line guard must not
    inadvertently freeze the offset for happy-path complete lines.
    """
    log_path = tmp_path / "actions.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps({
        "event_type": "round_end",
        "round": 1,
        "simulated_hours": 0,
    })
    _write_raw(log_path, line + "\n")

    state = _make_state()
    pos = SimulationRunner._read_action_log(
        str(log_path), 0, state, platform="twitter",
    )
    assert pos == log_path.stat().st_size
    assert state.twitter_current_round == 1
