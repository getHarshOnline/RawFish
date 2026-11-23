"""Real JSON-mode round-trip probe.

Mirrors the EXACT call pattern RawFish's report generator uses
against Ollama's OpenAI-compatible endpoint, so a failure here
predicts a failure on ``/api/sim/.../report/generate``. Run via
``make probe-llm`` or directly.

What it tests (4 distinct checks):

  CHECK 1 -- Ollama is up and serving the configured model.
  CHECK 2 -- ``response_format={"type":"json_object"}`` is honored:
             the model returns a string that ``json.loads`` accepts with no
             preamble, markdown fences, or trailing prose.
  CHECK 3 -- A schema-bearing system prompt is followed: the returned object
             carries the exact keys the prompt demanded, not just valid JSON of
             any shape.
  CHECK 4 -- The predictor-vocabulary refusal contract holds: when the prompt
             forbids predict/forecast wording, the model avoids it. This is a
             warning-only check because RawFish's language guard still catches
             leaks downstream.

Exit codes:
  0 -- all hard checks passed
  1 -- at least one hard check failed (check 1, 2, or 3)
  2 -- check 4 warned; hard checks passed

The output names every failing check so a swap-in candidate
(``mistral-small:24b-instruct-2501-q4_K_M``, ``gemma3:27b``, etc.)
is one ``.env.local`` edit + ``make pull-models`` + re-run away.
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib import error, request

# Defaults match docker-compose.local.yml + .env.local.template.
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen3-coder:30b"
HTTP_TIMEOUT = 120.0  # first hit may JIT-load the model into VRAM


def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _red(s: str) -> str:   return f"\033[31m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"
def _bold(s: str) -> str:  return f"\033[1m{s}\033[0m"


def _post_json(url: str, payload: dict, timeout: float = HTTP_TIMEOUT) -> dict:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('RAWFISH_LLM_API_KEY', 'local-llama-key')}",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_content(chat_response: dict) -> str:
    return chat_response["choices"][0]["message"]["content"]


def check1_ollama_serves_model(base_url: str, model: str) -> bool:
    """The configured ``model`` resolves on this Ollama instance."""
    print(_bold("\nCHECK 1 - Ollama is up and serving the configured model"))
    try:
        with request.urlopen(
            base_url.replace("/v1", "") + "/api/tags", timeout=10,
        ) as resp:
            tags = json.loads(resp.read().decode("utf-8")).get("models", [])
    except (error.URLError, OSError) as exc:
        print(_red(f"  ✗ Ollama unreachable at {base_url}: {exc}"))
        return False
    names = [m.get("name", "") for m in tags]
    if model not in names:
        print(_red(f"  ✗ Model {model!r} not in `ollama list`."))
        print(f"    Available: {', '.join(names) or '<none>'}")
        print(f"    Pull with: ollama pull {model}")
        return False
    print(_green(f"  ✓ {model} is loaded"))
    return True


def check2_json_mode_returns_parseable(base_url: str, model: str) -> tuple[bool, str | None]:
    """``response_format=json_object`` returns parseable JSON with no preamble."""
    print(_bold("\nCHECK 2 - response_format=json_object returns parseable JSON"))
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict JSON emitter. Output ONLY a single JSON "
                    "object. No markdown fences. No prose before or after. No "
                    "explanation."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Return the JSON object {\"status\": \"ok\", \"echo\": 42}."
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_tokens": 512,
    }
    started = time.perf_counter()
    try:
        body = _post_json(base_url + "/chat/completions", payload)
    except (error.HTTPError, error.URLError, OSError) as exc:
        print(_red(f"  ✗ HTTP error: {exc}"))
        return False, None
    elapsed = time.perf_counter() - started
    content = _extract_content(body)
    print(f"  raw response ({elapsed:.1f}s): {content!r}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        print(_red(f"  ✗ json.loads failed: {exc}"))
        return False, content
    if not isinstance(parsed, dict):
        print(_red(f"  ✗ Expected a JSON object, got {type(parsed).__name__}"))
        return False, content
    print(_green("  ✓ Response parses as a JSON object with no preamble"))
    return True, content


def check3_schema_is_followed(base_url: str, model: str) -> bool:
    """A schema-bearing system prompt is followed: exact keys + types."""
    print(_bold("\nCHECK 3 - Schema-bearing prompt is followed key-for-key"))
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a policy-pre-mortem report fragment emitter. "
                    "Return ONLY a single JSON object matching this schema:\n"
                    "  {\n"
                    "    \"claim_id\": <string, kebab-case>,\n"
                    "    \"text\": <string, single sentence>,\n"
                    "    \"hypothesis_tag\": <one of: simulated | inference "
                    "| source_grounded | assumption | requires_validation>,\n"
                    "    \"confidence\": <one of: low | medium | high>\n"
                    "  }\n"
                    "No markdown fences. No prose. No reasoning trace."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Emit one claim about a US polarized public-policy "
                    "scenario where a small business owner segment sees "
                    "trust drop by ~10%."
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_tokens": 1024,
    }
    try:
        body = _post_json(base_url + "/chat/completions", payload)
    except (error.HTTPError, error.URLError, OSError) as exc:
        print(_red(f"  ✗ HTTP error: {exc}"))
        return False
    content = _extract_content(body)
    print(f"  raw response: {content!r}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        print(_red(f"  ✗ json.loads failed: {exc}"))
        return False
    required = {"claim_id", "text", "hypothesis_tag", "confidence"}
    missing = required - set(parsed.keys() if isinstance(parsed, dict) else [])
    if missing:
        print(_red(f"  ✗ Missing required keys: {sorted(missing)}"))
        return False
    allowed_tags = {
        "simulated", "inference", "source_grounded",
        "assumption", "requires_validation",
    }
    if parsed.get("hypothesis_tag") not in allowed_tags:
        print(_red(
            f"  ✗ hypothesis_tag {parsed.get('hypothesis_tag')!r} not in "
            f"{sorted(allowed_tags)}",
        ))
        return False
    if parsed.get("confidence") not in {"low", "medium", "high"}:
        print(_red(
            f"  ✗ confidence {parsed.get('confidence')!r} not in "
            f"{{low, medium, high}}",
        ))
        return False
    print(_green(
        f"  ✓ Schema followed; hypothesis_tag={parsed['hypothesis_tag']!r}, "
        f"confidence={parsed['confidence']!r}",
    ))
    return True


def check4_predictor_refusal_held(base_url: str, model: str) -> bool:
    """Warning-only check: a 'do not use these words' instruction is respected."""
    print(_bold("\nCHECK 4 (warning-only) - Predictor-vocabulary refusal contract"))
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an audience response report fragment emitter. "
                    "Return ONLY a single JSON object with one key 'text' "
                    "whose value is a single sentence. ABSOLUTELY DO NOT use "
                    "the words: predict, prediction, forecast, will happen, "
                    "guarantee. Reframe as hypothesis / co-occurrence / "
                    "simulated. No markdown fences."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Describe the modeled reaction of a citizen segment to "
                    "a policy that raises a sales tax by 2%."
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_tokens": 512,
    }
    try:
        body = _post_json(base_url + "/chat/completions", payload)
    except (error.HTTPError, error.URLError, OSError) as exc:
        print(_yellow(f"  ! HTTP error: {exc} (warning-only check, continuing)"))
        return False
    content = _extract_content(body)
    try:
        parsed = json.loads(content)
        text = (parsed.get("text") if isinstance(parsed, dict) else content) or ""
    except json.JSONDecodeError:
        text = content
    banned = {"predict", "prediction", "forecast", "will happen", "guarantee"}
    lower = text.lower()
    leaked = sorted(b for b in banned if b in lower)
    print(f"  raw response: {text!r}")
    if leaked:
        print(_yellow(
            f"  ! Banned tokens leaked: {leaked} "
            "(RawFish's language guard would catch this at render time, "
            "but the model is not respecting the refusal list directly)",
        ))
        return False
    print(_green("  ✓ No predictor vocabulary in response"))
    return True


def main() -> int:
    base_url = os.environ.get("RAWFISH_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("RAWFISH_LLM_MODEL", DEFAULT_MODEL)
    print(_bold("RawFish — JSON-mode probe"))
    print(f"  endpoint: {base_url}")
    print(f"  model:    {model}")

    if not check1_ollama_serves_model(base_url, model):
        return 1
    ok2, _ = check2_json_mode_returns_parseable(base_url, model)
    if not ok2:
        return 1
    if not check3_schema_is_followed(base_url, model):
        return 1
    soft_ok = check4_predictor_refusal_held(base_url, model)

    print("")
    print(_green(_bold("  ALL HARD CHECKS PASS")))
    if not soft_ok:
        print(_yellow(
            "  Predictor-refusal check warned -- the language guard "
            "will catch leaks at render time, but consider a different "
            "model if you want the LLM itself to respect refusal lists.",
        ))
        return 2
    print(_green("  Soft predictor-refusal gate also passed"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
