"""Boot matrix smoke tests for ZEP_MODE.

Each case spawns a fresh ``python -c "from app import create_app; ..."``
subprocess with a minimal env so the conftest's default ``ZEP_API_KEY``
shim cannot leak into the contract under test. Cases:

* ``ZEP_MODE`` unset: boots; defaults to mock; generic lane catalog 200.
* ``ZEP_MODE=real`` + no key: boot succeeds; generic ``prepare``
  returns 400 ``ZEP_API_KEY_MISSING`` (the adapter seam is the
  canonical failure surface, not boot fail-fast).
* ``ZEP_MODE=bogus``: boot fails; ``validate()`` emits
  ``Invalid ZEP_MODE=...``; in-process ``test_client`` confirms the
  endpoint half returns 400 ``INVALID_ZEP_MODE``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


_CREATE_INTERNAL_POLICY_RUN = (
    "scenario = {\n"
    "  'persona': 'policy',\n"
    "  'scope': 'internal_org',\n"
    "  'title': 'Boot matrix',\n"
    "  'summary': 'Exercise generic prepare.',\n"
    "  'seed_text': 'An internal rollout is under review.',\n"
    "  'stress_question': 'What breaks first?',\n"
    "  'stakeholders': [{\n"
    "    'segment_id': 'employees',\n"
    "    'name': 'Employees',\n"
    "    'type': 'employee_cohort',\n"
    "    'interests': ['clarity'],\n"
    "    'incentives': ['avoid unnecessary risk'],\n"
    "    'constraints': ['limited time'],\n"
    "    'likely_support_reasons': ['clear benefit'],\n"
    "    'likely_opposition_reasons': ['uncertain rollout'],\n"
    "    'information_channels': ['internal update'],\n"
    "    'influence_level': 3,\n"
    "    'vulnerability_level': 3,\n"
    "    'compliance_propensity': 3,\n"
    "    'trust_level': 3,\n"
    "    'mobilization_capacity': 2,\n"
    "    'what_changes_their_mind': ['credible evidence'],\n"
    "  }],\n"
    "  'assumptions': ['The change is visible.'],\n"
    "  'success_criteria': ['Surface risk.'],\n"
    "  'failure_modes_to_watch': ['low trust'],\n"
    "  'run': {'ensemble_n': 1, 'run_seed': 42},\n"
    "  'drift_horizon_rounds': 1,\n"
    "}\n"
    "created = client.post(\n"
    "  '/api/sim/use-cases/internal_policy/simulation/create',\n"
    "  json={'scenario': scenario},\n"
    ")\n"
    "if created.status_code != 201:\n"
    "    print(json.dumps({'create_status': created.status_code, 'body': created.get_json()}))\n"
    "    raise SystemExit(2)\n"
    "sim_id = created.get_json()['data']['simulation_id']\n"
)


def _base_env() -> dict[str, str]:
    """Minimal env to satisfy non-Zep Config.validate() checks.

    PATH is required for the subprocess to find python; SECRET_KEY +
    LLM_API_KEY + API_KEY satisfy the other ``validate()`` rules so the
    only variable under test is ZEP_MODE / ZEP_API_KEY.

    FLASK_DEBUG=true keeps the SECRET_KEY production-strength rule from
    firing (the test uses the placeholder sentinel value).

    PYTHONPATH points at backend/ so ``from app import create_app``
    resolves the same module layout as pytest.

    RAWFISH_TEST_SILENT_SECRET_WARN suppresses the noisy stderr warning
    when SECRET_KEY happens to use the random fallback in a deeper code
    path; not strictly needed here but keeps test output clean.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(BACKEND_DIR),
        "LLM_API_KEY": "test-llm-key",
        "API_KEY": "test-api-key",
        "AUTH_ENABLED": "true",
        "FLASK_DEBUG": "true",
        "SECRET_KEY": "test-secret-32chars-test-secret-32",
        "RAWFISH_TEST_SILENT_SECRET_WARN": "1",
    }


def _run_python(code: str, env: dict[str, str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run ``python -c <code>`` with the given env. Returns the
    CompletedProcess so callers can assert on returncode / stdout /
    stderr. Does NOT raise on non-zero exit -- the boot-failure cases
    *expect* non-zero.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(BACKEND_DIR),
    )


# ---------------------------------------------------------------------------
# (a) ZEP_MODE unset -- mock default boots without ZEP_API_KEY
# ---------------------------------------------------------------------------


def test_mock_default_boots_without_zep_key():
    """The documented default (ZEP_MODE=mock) must actually boot when
    ZEP_API_KEY is unset. The legacy Config.validate() unconditionally
    required ZEP_API_KEY and create_app() raised RuntimeError in this
    case; this test pins the corrected behaviour.

    Also exercises ``GET /api/sim/use-cases`` over an in-process test_client
    inside the same subprocess so the boot is proven end-to-end (a boot
    that initializes Flask but cannot serve a single route is not a boot).
    The subprocess disables auth so the request does not need a key
    header; auth contract is covered elsewhere.
    """
    code = (
        "import os, json\n"
        "os.environ['AUTH_ENABLED'] = 'false'\n"
        "from app import create_app\n"
        "from app.config import Config\n"
        "app = create_app(Config)\n"
        "client = app.test_client()\n"
        "resp = client.get('/api/sim/use-cases')\n"
        "print(json.dumps({'status': resp.status_code, 'mode': Config.ZEP_MODE}))\n"
    )
    env = _base_env()
    # Explicitly DO NOT set ZEP_MODE or ZEP_API_KEY -- prove the default.
    env.pop("ZEP_MODE", None)
    env.pop("ZEP_API_KEY", None)
    result = _run_python(code, env)
    assert result.returncode == 0, (
        f"Mock default boot failed.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Last line of stdout is our JSON payload (logger noise may precede it).
    last_line = result.stdout.strip().splitlines()[-1]
    payload = json.loads(last_line)
    assert payload["status"] == 200, f"GET /api/sim/use-cases returned {payload['status']}"
    assert payload["mode"] == "mock"


# ---------------------------------------------------------------------------
# (b1) ZEP_MODE=real without ZEP_API_KEY -- boot must SUCCEED
# ---------------------------------------------------------------------------
#
# ZEP_API_KEY is enforced at the endpoint boundary, not at boot. The split
# below replaces a prior single ``test_real_mode_without_zep_key_fails_boot``
# test (which asserted that create_app() raised) so the boot half and the
# endpoint half can fail independently of each other and the failure mode
# the user actually experiences (400 at /simulation/prepare) is pinned by a
# dedicated test.


def test_real_mode_without_zep_key_boots_successfully():
    """Boot in real mode with no ZEP_API_KEY: create_app() returns cleanly.

    The absence of ZEP_API_KEY is NOT a boot-time failure; only the
    endpoint guard rejects requests (covered by the companion test below).
    A successful boot here is what allows a deploy to come up first and
    surface the key-missing error to the operator via a normal 400
    response, instead of crashlooping the process.
    """
    code = (
        "from app import create_app\n"
        "from app.config import Config\n"
        "app = create_app(Config)\n"
        "print('BOOT_OK')\n"
    )
    env = _base_env()
    env["ZEP_MODE"] = "real"
    env.pop("ZEP_API_KEY", None)
    result = _run_python(code, env)
    assert result.returncode == 0, (
        f"Boot should succeed when ZEP_MODE=real and ZEP_API_KEY is missing."
        f"\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "BOOT_OK" in result.stdout, (
        f"Boot did not reach BOOT_OK marker.\nstdout={result.stdout!r}"
    )
    # Defense: ensure validate() did NOT regress to the old fail-fast message.
    assert "ZEP_API_KEY is required when ZEP_MODE=real" not in result.stdout, (
        "validate() must no longer add the ZEP_API_KEY error (key check "
        "lives at the endpoint boundary)."
    )
    assert "ZEP_API_KEY is required when ZEP_MODE=real" not in result.stderr, (
        "validate() must no longer add the ZEP_API_KEY error (key check "
        "lives at the endpoint boundary)."
    )


# ---------------------------------------------------------------------------
# (b2) ZEP_MODE=real, no ZEP_API_KEY -- generic prepare returns 400
# ---------------------------------------------------------------------------


def test_real_mode_without_zep_key_prepare_endpoint_returns_400():
    """Endpoint-boundary enforcement of the missing ZEP_API_KEY.

    Companion to (b1): boot succeeded, but generic prepare
    must return a structured 400 ``ZEP_API_KEY_MISSING`` because the request
    touches the audience-store seam.
    """
    code = (
        "import os, json\n"
        "os.environ['AUTH_ENABLED'] = 'false'\n"
        "from app import create_app\n"
        "from app.config import Config\n"
        "app = create_app(Config)\n"
        "client = app.test_client()\n"
        + _CREATE_INTERNAL_POLICY_RUN +
        "resp = client.post(f'/api/sim/use-cases/internal_policy/simulation/{sim_id}/prepare')\n"
        "body = resp.get_json() or {}\n"
        "err = (body.get('error') or {}) if isinstance(body, dict) else {}\n"
        "print(json.dumps({'status': resp.status_code, 'code': err.get('code')}))\n"
    )
    env = _base_env()
    env["ZEP_MODE"] = "real"
    env.pop("ZEP_API_KEY", None)
    result = _run_python(code, env)
    assert result.returncode == 0, (
        f"Subprocess crashed.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    last_line = result.stdout.strip().splitlines()[-1]
    payload = json.loads(last_line)
    assert payload["status"] == 400, payload
    assert payload["code"] == "ZEP_API_KEY_MISSING", payload


# ---------------------------------------------------------------------------
# (c) ZEP_MODE=bogus -- boot must fail with INVALID_ZEP_MODE
# ---------------------------------------------------------------------------


def test_bogus_zep_mode_rejected_at_boot():
    """An unknown ZEP_MODE value must NOT silently fall through to mock
    (the prior behavior). Boot validation rejects it with a clear
    diagnostic so misconfigurations are visible at startup, not at the
    first /simulation/prepare call.
    """
    code = (
        "from app import create_app\n"
        "from app.config import Config\n"
        "try:\n"
        "    create_app(Config)\n"
        "    print('NO_RAISE')\n"
        "except RuntimeError as exc:\n"
        "    print('RAISED:' + str(exc))\n"
    )
    env = _base_env()
    env["ZEP_MODE"] = "production"  # plausible typo; must be rejected
    result = _run_python(code, env)
    assert result.returncode == 0, (
        f"Subprocess crashed unexpectedly.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "RAISED:" in result.stdout, (
        f"Expected boot to fail; got stdout={result.stdout!r}"
    )
    assert "Invalid ZEP_MODE" in result.stdout, (
        f"Wrong error message. stdout={result.stdout!r}"
    )
    assert "'production'" in result.stdout, (
        f"Error should echo the offending value. stdout={result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# (d) Endpoint guard companion -- ZEP_MODE changed AFTER boot still
#     rejected at generic prepare.
# ---------------------------------------------------------------------------
#
# Boot validation is one line of defense; the endpoint guard is the
# other. This case proves the endpoint half: a process that booted under
# mock can have ZEP_MODE flipped to a bogus value in-flight (e.g. via a
# runtime env tweak in a test harness or a config reload) and the next
# generic prepare call must still return a structured 400
# INVALID_ZEP_MODE rather than silently falling through to the mock branch.


def test_endpoint_rejects_bogus_zep_mode_after_boot():
    code = (
        "import os, json\n"
        "os.environ['AUTH_ENABLED'] = 'false'\n"
        "from app import create_app\n"
        "from app.config import Config\n"
        "app = create_app(Config)\n"
        "client = app.test_client()\n"
        + _CREATE_INTERNAL_POLICY_RUN +
        "# Flip ZEP_MODE post-boot; endpoint guard re-reads each request.\n"
        "os.environ['ZEP_MODE'] = 'bogus-mode'\n"
        "resp = client.post(f'/api/sim/use-cases/internal_policy/simulation/{sim_id}/prepare')\n"
        "body = resp.get_json() or {}\n"
        "err = (body.get('error') or {}) if isinstance(body, dict) else {}\n"
        "print(json.dumps({'status': resp.status_code, 'code': err.get('code')}))\n"
    )
    env = _base_env()
    env["ZEP_MODE"] = "mock"
    env.pop("ZEP_API_KEY", None)
    result = _run_python(code, env)
    assert result.returncode == 0, (
        f"Subprocess crashed.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    last_line = result.stdout.strip().splitlines()[-1]
    payload = json.loads(last_line)
    assert payload["status"] == 400, payload
    assert payload["code"] == "INVALID_ZEP_MODE", payload
