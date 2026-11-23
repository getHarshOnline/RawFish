"""Platform-level runner injection types.

Defines the :class:`SimulationRunnerFactory` Protocol and the
:class:`SimulationRunnerHandle` dataclass the platform's
:class:`app.simulation_platform.runner_adapter.RunnerAdapter` consumes.
The types live here so the platform adapter and lane services can share
the same injection contract without importing each other.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Protocol


# The four lifecycle states the policy handler may write into state.json
# at start time. ``starting`` is the in-flight state set
# BEFORE the factory call returns; ``running`` is the post-call default
# when the factory does not signal synchronous completion;
# ``completed`` / ``failed`` are the terminal hints the factory MAY
# return when it has already finished (test stubs use these).
TerminalStatus = Literal["running", "completed", "failed"]


@dataclass(frozen=True)
class SimulationRunnerHandle:
    """Immediate-return contract from :meth:`SimulationRunnerFactory.start`.

    Attributes:
        task_id:
            The runner-side task identifier. The policy handler persists
            this into :attr:`RunMetadata.runner_task_id` so /status can
            correlate the run with the runner's own bookkeeping.
        terminal_status:
            Optional terminal-state hint. ``None`` means "background
            execution kicked off; state.json should transition to
            ``running``". A non-``None`` value tells the handler the
            factory's ``start`` already finished synchronously and
            state.json should jump straight to that terminal value
            (test stubs use this).
        error:
            Free-form error string set ONLY when ``terminal_status ==
            "failed"`` so the handler can persist the failure cause into
            state.json. ``None`` otherwise.
    """

    task_id: str
    terminal_status: Optional[TerminalStatus] = None
    error: Optional[str] = None


class SimulationRunnerFactory(Protocol):
    """Inject-point used by generic start orchestration.

    Implementations are responsible for kicking off (or pretending to
    kick off) the runner. They MUST be cheap to call synchronously --
    the policy handler is on the request thread.
    """

    def start(
        self,
        *,
        simulation_id: str,
        run_dir: str,
        config: Dict[str, Any],
    ) -> SimulationRunnerHandle:
        """Spawn (or stub) the runner for ``simulation_id``."""
        ...


__all__ = [
    "SimulationRunnerFactory",
    "SimulationRunnerHandle",
    "TerminalStatus",
]
