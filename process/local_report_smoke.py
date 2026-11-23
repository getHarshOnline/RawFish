"""Live local-LLM report smoke for RawFish.

This smoke uses the Flask test client to drive the generic creator lane:

  create -> prepare -> start -> report/generate -> report read -> report chat

Unlike process/demo_smoke.py, this path calls the real provider router and
requires a reachable local Ollama-compatible model. Missing endpoint/model is a
clean skip by default; set RAWFISH_REQUIRE_LOCAL_REPORT_SMOKE=true to make that
condition fail.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any


_BASE = "/api/sim/use-cases/creator"
_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_DATA_DIR = "/tmp/rawfish_local_report_smoke"
_DEFAULT_MODEL = "qwen3-coder:30b"
_REQUIRE_ENVS = (
    "RAWFISH_REQUIRE_LOCAL_REPORT_SMOKE",
    "RAWFISH_LOCAL_REPORT_SMOKE_REQUIRED",
)
_EXTERNAL_KEY_ENVS = (
    "RAWFISH_LLM_API_KEY",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "COHERE_API_KEY",
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _required() -> bool:
    return any(_truthy(os.environ.get(name)) for name in _REQUIRE_ENVS)


def _strip_external_keys() -> None:
    for name in _EXTERNAL_KEY_ENVS:
        os.environ.pop(name, None)


def _creator_scenario() -> dict[str, Any]:
    return {
        "persona": "video_creator",
        "title": "Local report smoke",
        "summary": "A minimal creator scenario for the live local LLM smoke.",
        "seed_text": "We are launching a short-form video series for existing subscribers.",
        "stress_question": "Who engages, and what might be misunderstood?",
        "stakeholders": [
            {
                "segment_id": "fans",
                "name": "Loyal subscribers",
                "type": "loyal_subscriber",
                "what_changes_their_mind": [
                    "a clear explanation of the new format",
                ],
            },
            {
                "segment_id": "casuals",
                "name": "Casual viewers",
                "type": "casual_viewer",
                "what_changes_their_mind": [
                    "a concise opening hook",
                ],
            },
        ],
        "assumptions": ["the creator publishes on schedule"],
        "success_criteria": ["audience understands the format change"],
        "failure_modes_to_watch": ["the launch sounds overconfident"],
    }


def _response_data(response: Any) -> dict[str, Any]:
    body = response.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _response_detail(response: Any) -> str:
    body = response.get_json(silent=True)
    if body is None:
        return response.get_data(as_text=True)[:500]
    return json.dumps(body, sort_keys=True)[:700]


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    backend_root = repo_root / "backend"
    sys.path.insert(0, str(backend_root))

    base_url = os.environ.get("RAWFISH_LLM_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("RAWFISH_LLM_MODEL", _DEFAULT_MODEL)
    data_dir = Path(
        os.environ.get("RAWFISH_LOCAL_REPORT_SMOKE_DATA_DIR", _DEFAULT_DATA_DIR),
    )

    os.environ["AUTH_ENABLED"] = "false"
    os.environ["FLASK_DEBUG"] = "true"
    os.environ["RAWFISH_LLM_PROVIDER_MODE"] = "local_ollama"
    os.environ["RAWFISH_LLM_BASE_URL"] = base_url
    os.environ["RAWFISH_LLM_MODEL"] = model
    os.environ["ZEP_MODE"] = "mock"
    os.environ["SECRET_KEY"] = (
        "local-report-smoke-secret-key-with-32-characters"
    )
    os.environ.setdefault("POLICY_MAX_TOKENS_REPORT", "4096")
    os.environ.setdefault("RAWFISH_LLM_GENERATION_TIMEOUT_SEC", "120")
    os.environ.pop("AUTO_REPORT_GENERATE", None)
    _strip_external_keys()

    from app.simulation_platform.llm.router import (
        ProviderConfig,
        check_provider_readiness,
    )

    provider = ProviderConfig(
        mode="local_ollama",
        model=model,
        base_url=base_url,
    )

    print("RawFish local report smoke")
    print(f"  endpoint: {base_url}")
    print(f"  model:    {model}")
    print(f"  data_dir: {data_dir}")

    readiness = check_provider_readiness(provider, probe=True)
    if not readiness.ready:
        detail = readiness.reason or readiness.status
        message = (
            "SKIP: local provider is not ready "
            f"(status={readiness.status}, reason={detail})"
        )
        if _required():
            print(message.replace("SKIP:", "FAIL:", 1))
            return 1
        print(message)
        print(
            "Set RAWFISH_REQUIRE_LOCAL_REPORT_SMOKE=true to require this "
            "smoke in CI or a prepared local stack.",
        )
        return 0

    print(
        "  PASS  provider readiness "
        f"status={readiness.status}, latency_ms={readiness.latency_ms}",
    )

    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    from app import create_app
    from app.config import Config

    _strip_external_keys()
    Config.OASIS_SIMULATION_DATA_DIR = str(data_dir)
    flask_app = create_app(Config)
    _strip_external_keys()
    client = flask_app.test_client()

    errors: list[str] = []

    def step(name: str, ok: bool, detail: str = "") -> None:
        marker = "PASS" if ok else "FAIL"
        suffix = f"  {detail}" if detail else ""
        print(f"  {marker}  {name}{suffix}", flush=True)
        if not ok:
            errors.append(name)

    scenario = _creator_scenario()
    provider_payload = provider.public_dict()

    preflight = client.post(
        f"{_BASE}/evals/preflight_meter",
        json={
            "scenario": scenario,
            "rounds": 1,
            "ensemble_n": 1,
            "provider": provider_payload,
            "probe_provider": False,
        },
    )
    preflight_data = _response_data(preflight)
    readiness_data = preflight_data.get("provider_readiness", {})
    step(
        "preflight accepts local provider without external key",
        preflight.status_code == 200
        and preflight_data.get("provider", {}).get("mode") == "local_ollama"
        and "api_key" not in preflight_data.get("provider", {})
        and readiness_data.get("status") != "missing_external_key",
        f"status={preflight.status_code}, readiness={readiness_data.get('status')!r}",
    )
    if errors:
        print(_response_detail(preflight))
        return 1

    created = client.post(
        f"{_BASE}/simulation/create",
        json={
            "scenario": scenario,
            "provider": provider_payload,
            "preflight_meter": preflight_data,
        },
        headers={"Idempotency-Key": "rawfish-local-report-smoke-create"},
    )
    create_data = _response_data(created)
    sim_id = create_data.get("simulation_id")
    step(
        "create simulation",
        created.status_code == 201 and isinstance(sim_id, str) and sim_id,
        f"status={created.status_code}, sim_id={sim_id!r}",
    )
    if not isinstance(sim_id, str) or not sim_id:
        print(_response_detail(created))
        return 1

    prepared = client.post(
        f"{_BASE}/simulation/{sim_id}/prepare",
        headers={"Idempotency-Key": "rawfish-local-report-smoke-prepare"},
    )
    prepared_data = _response_data(prepared)
    step(
        "prepare simulation",
        prepared.status_code == 200 and prepared_data.get("phase") == "prepared",
        f"status={prepared.status_code}, phase={prepared_data.get('phase')!r}",
    )

    started = client.post(
        f"{_BASE}/simulation/{sim_id}/start",
        headers={"Idempotency-Key": "rawfish-local-report-smoke-start"},
    )
    started_data = _response_data(started)
    step(
        "start simulation",
        started.status_code == 200 and started_data.get("phase") == "completed",
        f"status={started.status_code}, phase={started_data.get('phase')!r}",
    )

    report = client.post(
        f"{_BASE}/simulation/{sim_id}/report/generate",
        json={"async": True},
        headers={"Idempotency-Key": "rawfish-local-report-smoke-report"},
    )
    report_data = _response_data(report)
    job_id = report_data.get("job_id")
    step(
        "enqueue report through real local LLM",
        report.status_code == 202 and isinstance(job_id, str) and bool(job_id),
        f"status={report.status_code}, job_id={job_id!r}",
    )
    if report.status_code != 202 or not job_id:
        return 1

    job_data: dict[str, Any] = {}
    for _attempt in range(180):
        job = client.get(f"{_BASE}/simulation/{sim_id}/report/jobs/{job_id}")
        job_data = _response_data(job)
        if job.status_code == 200 and job_data.get("status") in {"succeeded", "failed"}:
            break
        time.sleep(1)
    report_response = job_data.get("response") if isinstance(job_data, dict) else {}
    report_data = (
        report_response.get("data")
        if isinstance(report_response, dict)
        and isinstance(report_response.get("data"), dict)
        else report_response
    )
    report_path = report_data.get("report_path") if isinstance(report_data, dict) else None
    step(
        "complete async report through real local LLM",
        job_data.get("status") == "succeeded" and report_path == "creator_report.json",
        f"status={job_data.get('status')!r}, report_path={report_path!r}",
    )

    read = client.get(f"{_BASE}/simulation/{sim_id}/report")
    read_data = _response_data(read)
    step(
        "read persisted report",
        read.status_code == 200
        and read_data.get("simulation_id") == sim_id
        and isinstance(read_data.get("sections"), list)
        and bool(read_data.get("sections")),
        f"status={read.status_code}, sections={len(read_data.get('sections') or [])}",
    )

    chat = client.post(
        f"{_BASE}/simulation/{sim_id}/report/chat",
        json={
            "message": "What is the sharpest risk in this report?",
            "chat_history": [],
        },
        headers={"Idempotency-Key": "rawfish-local-report-smoke-chat"},
    )
    chat_data = _response_data(chat)
    step(
        "chat with persisted report",
        chat.status_code == 200
        and isinstance(chat_data.get("response"), str)
        and bool(chat_data.get("response", "").strip())
        and isinstance(chat_data.get("sources"), list),
        f"status={chat.status_code}, response_len={len(chat_data.get('response') or '')}",
    )

    run_dir = data_dir / "creator" / sim_id
    state_path = run_dir / "state.json"
    report_file = run_dir / "creator_report.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    step(
        "state persisted local provider without api_key",
        state.get("llm_provider", {}).get("mode") == "local_ollama"
        and "api_key" not in state.get("llm_provider", {}),
    )
    step(
        "runtime external key aliases absent",
        all(os.environ.get(name) in (None, "") for name in _EXTERNAL_KEY_ENVS),
    )
    step("report artifact exists", report_file.is_file(), str(report_file))

    if errors:
        print(f"\n--- RAWFISH LOCAL REPORT SMOKE FAILED: {len(errors)} failure(s) ---")
        for name in errors:
            print(f"   * {name}")
        if report.status_code != 200:
            print(_response_detail(report))
        if chat.status_code != 200:
            print(_response_detail(chat))
        return 1

    print("\n--- RAWFISH LOCAL REPORT SMOKE: ALL CHECKS PASS ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
