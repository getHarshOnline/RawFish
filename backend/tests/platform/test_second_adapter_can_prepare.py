"""Conformance test: a second adapter can publish its own prepare
contract through the platform seam.

The point of the platform extraction is that a new use case can plug
in without editing the platform glue. The route layer is not yet
generic-dispatched (that bridging step is out of scope for this
batch of conformance tests -- the policy route still calls the
adapter directly), so this test asserts the registry-level call path
rather than the HTTP path:

* register a second fake adapter alongside the existing ones,
* assert :func:`simulation_platform.registry.get` returns it under
  the right id,
* assert its :meth:`artifact_names` differ from policy's (so a future
  generic dispatcher cannot accidentally collapse the two), and
* call :meth:`write_prepare_artifacts` on the registry-looked-up
  instance and assert the fake records the call (proving the
  registry hands back a working object rather than a stale alias).

When the HTTP layer gains a generic dispatcher this test should grow
a sibling that exercises the HTTP path with a ``use_case_id`` query
parameter; for now the registry-level call is what the platform
boundary actually guarantees.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from app.simulation_platform.registry import get, register
from app.simulation_platform.tests_helpers import FakeUseCase
from app.simulation_platform.use_case import SimulationUseCase


class SecondFakeUseCase(FakeUseCase):
    """Subclass that publishes its own artifact filenames.

    Subclassing :class:`FakeUseCase` keeps the Protocol conformance
    "free" (every Protocol method is satisfied by the parent); we only
    override :meth:`artifact_names` so the registry-level distinction
    between this adapter and the policy adapter is observable. The
    ``recorded_prepare_run_dirs`` list captures each run_dir passed to
    :meth:`write_prepare_artifacts` so the test can assert the
    registry returned this instance (not a stale alias) by checking
    the side effect landed on the right object.
    """

    def __init__(self, uid: str = "second-fake") -> None:
        super().__init__(uid=uid)
        self.recorded_prepare_run_dirs: List[Path] = []

    def artifact_names(self) -> Dict[str, str]:
        return {
            "report_json": "second_report.json",
            "manifest": "second_manifest.json",
        }

    def write_prepare_artifacts(
        self,
        scenario: Any,
        run_dir: Path,
    ) -> Any:
        # Record on the subclass-owned list so the test can prove the
        # registry returned THIS instance rather than the parent
        # FakeUseCase's class-level field (which would mask aliasing).
        self.recorded_prepare_run_dirs.append(run_dir)
        return {"artifacts": {"report_json": "second_report.json"}}


def test_second_adapter_registers_with_distinct_artifact_names(tmp_path) -> None:
    """Registry round-trip: ``register`` then ``get`` returns the same
    instance, and its ``artifact_names`` are distinct from policy's.

    The HTTP layer is not yet registry-dispatched -- the policy route
    calls the adapter directly. This test therefore exercises the
    registry-level contract: a new adapter can join the platform and
    a downstream caller that goes through ``registry.get(uid)`` gets
    back the right object with its own artifact filenames. When the
    generic dispatcher lands, a sibling test should add the HTTP-path
    assertion; until then, the registry contract is what the platform
    actually guarantees and is what we pin here.
    """
    second = SecondFakeUseCase(uid="second-fake-prepare")
    register(second)

    looked_up = get("second-fake-prepare")
    assert looked_up is second, (
        "registry.get must return the exact instance that was registered"
    )
    # Structural Protocol check (the registry relies on this for
    # isinstance-style dispatch in the future).
    assert isinstance(looked_up, SimulationUseCase)

    # Artifact names must differ from policy's so a future generic
    # dispatcher cannot accidentally collapse the two adapters'
    # on-disk layouts onto a shared set of filenames.
    second_names = looked_up.artifact_names()
    assert second_names == {
        "report_json": "second_report.json",
        "manifest": "second_manifest.json",
    }

    # Exercise the prepare path through the registry-looked-up object
    # (NOT through the local ``second`` binding) -- this is what proves
    # the registry didn't hand back a stale alias. We rely on the
    # subclass-owned ``recorded_prepare_run_dirs`` list rather than
    # ``FakeUseCase.prepare_artifacts_written`` so a misuse where the
    # registry returns a parent-class instance would surface here.
    run_dir = tmp_path / "run-001"
    run_dir.mkdir()
    result = looked_up.write_prepare_artifacts(scenario=None, run_dir=run_dir)

    assert isinstance(result, dict)
    assert "artifacts" in result
    # ``looked_up`` is typed as the Protocol; narrow it to the concrete
    # subclass so the recorded-list attribute is reachable without a
    # type-checker complaint.
    assert isinstance(looked_up, SecondFakeUseCase)
    assert looked_up.recorded_prepare_run_dirs == [run_dir]


def test_second_adapter_artifact_names_do_not_collide_with_policy(
    monkeypatch,
) -> None:
    """The second adapter's ``artifact_names`` keys overlap by purpose
    but the filenames differ from the policy adapter's.

    Two adapters that emit ``report_json`` is fine (that's the logical
    id contract); two adapters that pick the SAME filename inside the
    same run_dir would collide on disk if a future runner ever shared
    run directories. We assert the filename divergence so that latent
    risk surfaces at registration time rather than at the first cross-
    use-case run.

    Boots through the app factory (via ``make_app``) so the policy
    adapter is registered first; the second adapter is registered
    after.
    """
    # Import inside the test so the module-import-time check in
    # ``test_fake_use_case_registration.py`` (which forbids
    # ``app.policy`` imports at module top) is not affected by this
    # file's policy import. This file is allowed to import from
    # ``app.policy`` -- only the fake-registration file is not.
    from tests._helpers import make_app

    make_app(monkeypatch)
    policy_name_maps = [
        get("internal_policy").artifact_names(),
        get("public_policy").artifact_names(),
    ]

    second = SecondFakeUseCase(uid="second-fake-collision-check")
    register(second)
    second_names = get("second-fake-collision-check").artifact_names()

    for policy_names in policy_name_maps:
        overlapping_logical_ids = set(policy_names) & set(second_names)
        for logical_id in overlapping_logical_ids:
            assert policy_names[logical_id] != second_names[logical_id], (
                f"filename collision on logical_id {logical_id!r}: "
                f"policy uses {policy_names[logical_id]!r}, second uses "
                f"{second_names[logical_id]!r} -- pick distinct filenames "
                "so a shared run_dir cannot overwrite one adapter's output."
            )
