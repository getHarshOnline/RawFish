# Extending RawFish

Add a lane when a decision needs different scenario fields, groups or report questions. Reuse the cockpit and generic API; implement the lane's interpretation behind an adapter. In this prototype, adding a distinct lane also requires frontend changes because Launch and the run store still branch on the three known lane ids.

The [architecture](architecture.md) explains the shared lifecycle. Manifest-only extension is a [rebuild target](POLICY_PRODUCT_SPEC.md), not current behavior.

## Inspect an existing adapter

Install the [source dependencies](../README.md#build-and-test), then run this from the repository root. It inspects registration without starting a server or model:

```sh
cd backend
.venv/bin/python - <<'PYCODE'
from app.creator.adapter import CREATOR_USE_CASE
from app.simulation_platform.registry import get, register

register(CREATOR_USE_CASE)
register(CREATOR_USE_CASE)
adapter = get("creator")
print(adapter.use_case_id())
print(adapter.catalog().lane_manifest.lane_id)
print(adapter.artifact_names()["report_json"])
print(adapter.report_schema.__name__)
PYCODE
```

Expected output:

```text
creator
creator
creator_report.json
CreatorReport
```

Configuration import can warn about an unset secret key; this example does not construct Flask. Registering the same singleton twice leaves one entry. The registry checks required members, not their signatures or behavior.

<a id="adding-a-lane"></a>

## Implement the lane

::::steps

:::step "Define its catalog and manifests"

Choose a short id. Use the [catalog contracts](../backend/app/simulation_platform/catalog.py) for the response envelope and required `lane_manifest`. Supply the lane manifest, statistical manifest and cohort pack. Declare only statistical controls the runtime actually uses.

The Creator [lane manifest](../frontend/src/lanes/creator/lane_manifest.json) and [statistical manifest](../frontend/src/lanes/creator/statistical.manifest.json) show the current shape. Do not copy their declarations without checking the new lane's consumers.

:::

:::step "Implement the lifecycle adapter"

Follow the [SimulationUseCase protocol](../backend/app/simulation_platform/use_case.py) and [Creator adapter](../backend/app/creator/adapter.py). Keep `validate_scenario` and `rehydrate_scenario` consistent so creation and reopening return the same lane-owned type.

Publish an `artifact_names` mapping for files the adapter actually writes. `report_schema` validates stored reads; `report_draft_schema` can limit model output to model-owned fields. Let the application supply run identity and provenance.

`generate_report` returns an in-memory object and must use the supplied `llm_client`. Dispatch owns persistence and keeps the selected provider, audit and output validation. Choose the `guardrail_capabilities` hooks and declare whether the adapter already runs them, so dispatch does not apply side effects twice.

:::

:::step "Connect real execution and evidence"

Choose the runner return value deliberately. `None` tells dispatch that in-process work has finished; `"running"` leaves an active subprocess running. Unsupported sensitivity should raise `NotImplementedError`, which becomes `501 CAPABILITY_UNSUPPORTED`.

Creator returns `None` after writing a deterministic marker. Its report generator discards the evidence catalog. Copying it unchanged gives a lane mock completion and generated prose, not executed agent interactions. Implement the runner and evidence consumption the new lane's findings require.

:::

:::step "Register it and update the cockpit seams"

Add the adapter to [built-in registration](../backend/app/simulation_platform/builtin_lanes.py). The [registry](../backend/app/simulation_platform/registry.py) refuses a different adapter under an occupied id.

Update [Launch](../frontend/src/cockpit/HomeView.vue) for seed presets and parser identity. Update [the run store](../frontend/src/cockpit/runStore.js) for parser identity, scenario payload construction and stakeholder fallbacks. Both currently encode the known lanes directly.

Keep traffic under `/api/sim/use-cases/<lane_id>/...`. Do not add per-lane browser routes or require a particular external model provider.

:::

:::step "Test the lane's behavior"

Use `backend/tests/creator/test_creator_adapter_conformance.py` and `backend/tests/platform/test_creator_lifecycle_via_generic_dispatch.py` as examples for validation, reopening, artifact paths and runner status. Add the new lane's own cases, and test each lane adapter's run and report separately. Frontend tests must cover its parsing and payloads rather than silently using a known policy shape.

Run `make ci` with a local model available. Then follow the [six-step cockpit task](using-rawfish.md#continue-through-the-six-cockpit-steps) using a suitable scenario. Inspect saved configuration, actual runner output and the report's support.

:::

::::

## Reuse shared utilities without assuming a portable host

The provider router, preflight payloads, language guard, claim labels, scale guard, idempotency utilities and artifact hashing can serve another lane. Flask's app factory, the OASIS runner bridge, engine services and the Graphiti/Neo4j integration depend on this host and need adaptation elsewhere.

A catalog entry proves discovery, not successful execution. Keep unsupported capabilities explicit and preserve the [guardrail distinctions](GUARDRAILS.md) in any new interface.
