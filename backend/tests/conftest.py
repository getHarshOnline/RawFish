import sys
from pathlib import Path

# Make `backend/app` importable without installing the package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def _set_dummy_env(monkeypatch, **overrides):
    """Default env that satisfies Config.validate() for boot-only tests."""
    defaults = {
        "LLM_API_KEY": "test-llm-key",
        "ZEP_API_KEY": "test-zep-key",
        "API_KEY": "test-api-key",
        "AUTH_ENABLED": "true",
        "FLASK_DEBUG": "false",
        "SECRET_KEY": "test-secret-32chars-test-secret-32",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


def make_app(monkeypatch, **env_overrides):
    """Reload Config + create_app under a clean env snapshot."""
    _set_dummy_env(monkeypatch, **env_overrides)
    # Force Config re-read by reloading the modules
    import importlib
    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])
    # Re-sync cached Config references inside already-imported submodules that
    # bound `Config` at their original import time.
    import app.config as _cfg_mod
    for _modname in ("app.simulation_platform.state_machine", "app.simulation_platform.idempotency"):
        if _modname in sys.modules:
            # setattr (not direct attribute assignment) because Pyright
            # statically knows ModuleType doesn't declare ``Config``; the
            # rebind is dynamic-by-design and setattr is the canonical
            # Python idiom for this kind of cross-module patch.
            setattr(sys.modules[_modname], "Config", _cfg_mod.Config)
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
    from app import create_app
    from app.config import Config
    return create_app(Config)
