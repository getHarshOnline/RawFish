"""RawFish mock-mode wire smoke.

Boots the Flask app with no Neo4j and no live LLM, then walks the RawFish
generic lane surface:

  GET  /use-cases/<lane>/catalog
  GET  /use-cases/<lane>/personas
  POST /use-cases/<lane>/evals/preflight_meter
  POST /use-cases/creator/simulation/create
  POST /use-cases/creator/simulation/<id>/prepare
  POST /use-cases/creator/simulation/<id>/start
  GET  /use-cases/creator/simulation/<id>/status

The smoke intentionally avoids removed aggregate and wizard routes.
It also does not call report generation; that path is owned by the
provider-router tests and live LLM probes.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


_SMOKE_DATA_DIR = "/tmp/rawfish_demo_smoke"
_BASE = "/api/sim/use-cases"


def _creator_scenario() -> dict[str, Any]:
    return {
        "persona": "video_creator",
        "title": "Creator smoke scenario",
        "summary": "Minimal creator lane scenario for the RawFish wire smoke.",
        "seed_text": "A short launch note for a new long-form video.",
        "stress_question": "Where might the audience misread the intent?",
        "stakeholders": [
            {
                "segment_id": "loyal",
                "name": "Loyal subscribers",
                "type": "loyal_subscriber",
                "what_changes_their_mind": ["transparent follow-up"],
            }
        ],
        "assumptions": ["the creator publishes on schedule"],
        "success_criteria": ["audience understands the core point"],
        "failure_modes_to_watch": ["tone reads as dismissive"],
    }


def main() -> int:
    import shutil

    shutil.rmtree(_SMOKE_DATA_DIR, ignore_errors=True)
    os.makedirs(_SMOKE_DATA_DIR, exist_ok=True)

    os.environ["OASIS_SIMULATION_DATA_DIR"] = _SMOKE_DATA_DIR
    os.environ["AUTH_ENABLED"] = "false"
    os.environ["ZEP_MODE"] = "mock"
    os.environ["FLASK_ENV"] = "development"
    os.environ["FLASK_DEBUG"] = "true"
    os.environ["SECRET_KEY"] = (
        "demo-smoke-secret-key-with-32-characters-of-padding-yes-really"
    )
    os.environ["RAWFISH_LLM_PROVIDER_MODE"] = "local_ollama"
    os.environ["RAWFISH_LLM_MODEL"] = "qwen3-coder:30b"
    os.environ["RAWFISH_LLM_BASE_URL"] = "http://localhost:11434/v1"
    os.environ.pop("RAWFISH_LLM_API_KEY", None)
    for legacy_key in ("LLM_API" + "_KEY",):
        os.environ.pop(legacy_key, None)
    os.environ.pop("AUTO_REPORT_GENERATE", None)

    backend_root = Path(__file__).resolve().parents[1] / "backend"
    sys.path.insert(0, str(backend_root))

    from app import create_app
    from app.config import Config

    Config.OASIS_SIMULATION_DATA_DIR = _SMOKE_DATA_DIR
    flask_app = create_app()
    client = flask_app.test_client()

    print(f"data_dir={_SMOKE_DATA_DIR}", flush=True)
    errors: list[str] = []

    def step(name: str, ok: bool, detail: str = "") -> None:
        marker = "PASS" if ok else "FAIL"
        suffix = f"  {detail}" if detail else ""
        print(f"  {marker}  {name}{suffix}", flush=True)
        if not ok:
            errors.append(name)

    def data(response):
        body = response.get_json(silent=True) or {}
        return body.get("data") if isinstance(body, dict) else None

    for lane in ("creator", "internal_policy", "public_policy"):
        r = client.get(f"{_BASE}/{lane}/catalog")
        body = data(r) or {}
        step(
            f"GET {lane}/catalog carries matching lane_manifest",
            r.status_code == 200
            and body.get("use_case_id") == lane
            and body.get("lane_manifest", {}).get("lane_id") == lane,
            f"status={r.status_code}",
        )

    r = client.get(f"{_BASE}/creator/personas")
    creator_personas = (data(r) or {}).get("personas", [])
    step(
        "GET creator/personas exposes creator rows",
        r.status_code == 200
        and any(row.get("persona") == "video_creator" for row in creator_personas),
        f"status={r.status_code}, count={len(creator_personas)}",
    )

    scenario = _creator_scenario()
    r = client.post(
        f"{_BASE}/creator/evals/preflight_meter",
        json={
            "scenario": scenario,
            "rounds": 1,
            "ensemble_n": 1,
            "provider": {
                "mode": "local_ollama",
                "model": "qwen3-coder:30b",
                "base_url": "http://localhost:11434/v1",
            },
        },
    )
    preflight = data(r) or {}
    readiness = preflight.get("provider_readiness", {})
    step(
        "local preflight does not require external credentials",
        r.status_code == 200
        and preflight.get("provider", {}).get("mode") == "local_ollama"
        and readiness.get("status") != "missing_external_key"
        and preflight.get("estimated_external_cost") is None,
        f"status={r.status_code}, readiness={readiness.get('status')!r}",
    )

    r = client.post(
        f"{_BASE}/creator/evals/preflight_meter",
        json={
            "scenario": scenario,
            "provider": {
                "mode": "external",
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        },
    )
    external_preflight = data(r) or {}
    external_readiness = external_preflight.get("provider_readiness", {})
    step(
        "external preflight blocks missing key only for external mode",
        r.status_code == 200
        and external_readiness.get("ready") is False
        and external_readiness.get("status") == "missing_external_key",
        f"status={r.status_code}, readiness={external_readiness.get('status')!r}",
    )

    create_headers = {"Idempotency-Key": "rawfish-smoke-create-001"}
    r = client.post(
        f"{_BASE}/creator/simulation/create",
        json={"scenario": scenario, "preflight_meter": preflight},
        headers=create_headers,
    )
    created = data(r) or {}
    sim_id = created.get("simulation_id")
    step(
        "POST creator/simulation/create",
        r.status_code == 201 and isinstance(sim_id, str) and sim_id,
        f"status={r.status_code}, sim_id={sim_id!r}",
    )
    if not sim_id:
        return 1

    r = client.post(
        f"{_BASE}/creator/simulation/create",
        json={"scenario": scenario, "preflight_meter": preflight},
        headers=create_headers,
    )
    replay = data(r) or {}
    step(
        "create replay returns same simulation_id",
        r.status_code == 201 and replay.get("simulation_id") == sim_id,
        f"status={r.status_code}, replay={replay.get('simulation_id')!r}",
    )

    r = client.post(
        f"{_BASE}/creator/simulation/{sim_id}/prepare",
        json={},
        headers={"Idempotency-Key": "rawfish-smoke-prepare-001"},
    )
    prepared = data(r) or {}
    step(
        "POST creator/simulation/<id>/prepare",
        r.status_code == 200 and prepared.get("phase") == "prepared",
        f"status={r.status_code}, phase={prepared.get('phase')!r}",
    )
    run_dir = Path(_SMOKE_DATA_DIR) / "creator" / sim_id
    step(
        "prepare artifact written",
        any(run_dir.glob("*prepare.json")),
    )

    r = client.post(
        f"{_BASE}/creator/simulation/{sim_id}/start",
        json={},
        headers={"Idempotency-Key": "rawfish-smoke-start-001"},
    )
    started = data(r) or {}
    step(
        "POST creator/simulation/<id>/start",
        r.status_code == 200 and started.get("phase") == "completed",
        f"status={r.status_code}, phase={started.get('phase')!r}",
    )

    r = client.get(f"{_BASE}/creator/simulation/{sim_id}/status")
    status = data(r) or {}
    step(
        "GET creator/simulation/<id>/status completed",
        r.status_code == 200 and status.get("phase") == "completed",
        f"status={r.status_code}, phase={status.get('phase')!r}",
    )

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    step(
        "run state persists selected provider snapshot",
        state.get("llm_provider", {}).get("mode") == "local_ollama",
    )
    step(
        "run state persists preflight snapshot",
        isinstance(state.get("preflight_meter"), dict)
        and state["preflight_meter"].get("scale_product") == 1,
    )

    if errors:
        print(f"\n--- RAWFISH SMOKE FAILED: {len(errors)} failure(s) ---", flush=True)
        for error in errors:
            print(f"   * {error}", flush=True)
        return 1

    print("\n--- RAWFISH SMOKE: ALL CHECKS PASS ---", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
