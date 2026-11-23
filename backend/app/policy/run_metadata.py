"""Policy-lane compatibility re-export for run metadata.

The operational model lives in ``app.simulation_platform.run_metadata``.
This module remains as a narrow import bridge for policy-lane code and
older tests that still import ``app.policy.run_metadata`` directly.
"""

from __future__ import annotations

from app.simulation_platform.run_metadata import *  # noqa: F401,F403
