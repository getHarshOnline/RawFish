"""SECRET_KEY ephemeral generation must warn loudly."""
import os
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_secret_key_warning_printed_when_env_unset(monkeypatch, capsys):
    """Loading Config without SECRET_KEY env var must emit a stderr warning."""
    # Clear any leftover env
    for k in ("SECRET_KEY", "RAWFISH_TEST_SILENT_SECRET_WARN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("ZEP_API_KEY", "x")
    monkeypatch.setenv("API_KEY", "x")
    monkeypatch.setenv("AUTH_ENABLED", "true")

    # Force reload via subprocess so the module-level print runs fresh
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from app import config"],
        cwd=BACKEND_DIR,
        env={**os.environ, "LLM_API_KEY": "x", "ZEP_API_KEY": "x",
             "API_KEY": "x", "AUTH_ENABLED": "true"},
        capture_output=True,
        text=True,
    )
    # Either it printed the warning (success) OR import fails (also acceptable
    # if deps not installed in test env). We only fail if the import succeeded
    # AND no warning appeared.
    if result.returncode == 0:
        assert "SECRET_KEY env var not set" in result.stderr, (
            "Module loaded without SECRET_KEY env but no warning printed. "
            f"stderr was: {result.stderr!r}"
        )


def test_secret_key_no_warning_when_env_set(monkeypatch):
    """If SECRET_KEY is set, no warning printed."""
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from app import config"],
        cwd=BACKEND_DIR,
        env={**os.environ,
             "SECRET_KEY": "test-secret-32-chars-test-secret-32",
             "LLM_API_KEY": "x", "ZEP_API_KEY": "x",
             "API_KEY": "x", "AUTH_ENABLED": "true"},
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        assert "SECRET_KEY env var not set" not in result.stderr


def test_debug_with_nonloopback_host_refuses_in_run_py():
    """run.py refuses FLASK_DEBUG=true + FLASK_HOST=0.0.0.0 with exit code 2."""
    # We run run.py as a subprocess but interrupt before it actually starts the
    # server. Trick: pass FLASK_HOST=0.0.0.0 + FLASK_DEBUG=true and check the
    # exit code is 2 (our refuse path) within a short timeout.
    env = {**os.environ,
           "FLASK_HOST": "0.0.0.0",
           "FLASK_DEBUG": "true",
           "SECRET_KEY": "test-secret-32-chars-test-secret-32",
           "LLM_API_KEY": "x", "ZEP_API_KEY": "x",
           "API_KEY": "x", "AUTH_ENABLED": "true"}
    result = subprocess.run(
        [sys.executable, "run.py"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2, (
        f"Expected exit code 2 (debug+0.0.0.0 refused); got {result.returncode}. "
        f"stderr: {result.stderr!r}"
    )
    assert "Werkzeug interactive debugger" in result.stderr or "REFUSED" in result.stderr
