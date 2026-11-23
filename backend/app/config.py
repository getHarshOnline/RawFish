"""
Configuration management
Loads configuration uniformly from the .env file at the project root
"""

import importlib.util as _importlib_util
import os
import pathlib as _pathlib
from dotenv import load_dotenv

# ZEP_MODE parsing SSoT lives in ``app.utils.runtime_env``.
# A normal ``from .utils.runtime_env import ...`` triggers ``app/utils/__init__``
# which re-exports LLMClient -> Config, a partial-init ImportError.
# Load runtime_env.py directly from disk bypassing the package __init__ so
# Config can use the SSoT parser at class-def time without circularity.
# We register the resulting module in ``sys.modules`` under the canonical
# dotted name so the SAME module object (and therefore the SAME
# ``InvalidZepMode`` class identity) is reused when other modules do the
# normal ``from app.utils.runtime_env import ...``.
import sys as _sys

_RUNTIME_ENV_PATH = (
    _pathlib.Path(__file__).parent / "utils" / "runtime_env.py"
)
if "app.utils.runtime_env" in _sys.modules:
    _runtime_env = _sys.modules["app.utils.runtime_env"]
else:
    _spec = _importlib_util.spec_from_file_location(
        "app.utils.runtime_env", _RUNTIME_ENV_PATH,
    )
    assert _spec is not None and _spec.loader is not None
    _runtime_env = _importlib_util.module_from_spec(_spec)
    _sys.modules["app.utils.runtime_env"] = _runtime_env
    _spec.loader.exec_module(_runtime_env)

InvalidZepMode = _runtime_env.InvalidZepMode
parse_zep_mode = _runtime_env.parse_zep_mode

# Load the .env file from the project root
# Path: RawFish/.env (relative to backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # If there is no .env at the project root, fall back to environment variables (used in production)
    load_dotenv(override=True)


class Config:
    """Flask configuration class"""

    # Flask configuration
    # SECRET_KEY: random per-start fallback (safer than a hardcoded default,
    # but ephemeral and warns loudly so the user sees this is happening).
    # In production (DEBUG=false) MUST be set explicitly via env (see validate()).
    # A stderr warning fires at module load when the fallback engages so
    # misconfiguration is visible at boot rather than only at first signed
    # session.
    _SECRET_KEY_FROM_ENV = os.environ.get('SECRET_KEY')
    if _SECRET_KEY_FROM_ENV:
        SECRET_KEY = _SECRET_KEY_FROM_ENV
    else:
        SECRET_KEY = os.urandom(32).hex()
        # Use sys.stderr so this appears at boot even before logger setup.
        # Suppress only when explicitly under pytest (sentinel env var).
        if not os.environ.get('RAWFISH_TEST_SILENT_SECRET_WARN'):
            import sys as _sys
            print(
                "WARNING: SECRET_KEY env var not set; using EPHEMERAL random key. "
                "Sessions/signed cookies break across restarts. "
                "Set SECRET_KEY in .env for any non-throwaway use.",
                file=_sys.stderr,
            )
    # Safe default: DEBUG off by default to avoid exposing the Werkzeug
    # interactive debugger (remote RCE) to the network.
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'

    # API authentication: every /api/* endpoint requires X-API-Key (or Bearer token).
    # AUTH_ENABLED defaults to true; local dev may set AUTH_ENABLED=false explicitly.
    # Fail-closed parsing: only explicit false/0/no/off disables auth;
    # any other value (including whitespace, typos, 'true\n', '1', 'yes')
    # is treated as enabled, preventing env-config mistakes from silently
    # falling back to zero auth.
    API_KEY = os.environ.get('API_KEY')
    AUTH_ENABLED = os.environ.get('AUTH_ENABLED', 'true').strip().lower() not in ('false', '0', 'no', 'off')

    # CORS allowed origins: no wildcard '*' by default. Local frontend
    # dev/preview origins only; in production set ALLOWED_ORIGINS as
    # comma-separated frontend domains (e.g. https://app.example.com).
    # '*' is still settable explicitly but not recommended.
    ALLOWED_ORIGINS = [
        o.strip() for o in os.environ.get(
            'ALLOWED_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000'
        ).split(',') if o.strip()
    ]


    # JSON configuration - disable ASCII escaping so non-ASCII characters render directly (instead of \uXXXX)
    JSON_AS_ASCII = False

    # RawFish LLM provider configuration.
    #
    # Local modes must boot without external credentials. The LLM_*
    # names remain accepted as environment aliases because local
    # OpenAI-compatible servers (including Ollama's /v1 shim) use that
    # vocabulary, but validation is now provider-mode aware.
    RAWFISH_LLM_PROVIDER_MODE = os.environ.get(
        'RAWFISH_LLM_PROVIDER_MODE',
        os.environ.get('LLM_PROVIDER_MODE', 'local_ollama'),
    )
    RAWFISH_LLM_MODEL = os.environ.get(
        'RAWFISH_LLM_MODEL',
        os.environ.get('LLM_MODEL_NAME', 'qwen3-coder:30b'),
    )
    RAWFISH_LLM_BASE_URL = os.environ.get(
        'RAWFISH_LLM_BASE_URL',
        os.environ.get('LLM_BASE_URL', 'http://localhost:11434/v1'),
    )
    RAWFISH_LLM_PROVIDER = os.environ.get(
        'RAWFISH_LLM_PROVIDER',
        os.environ.get('LLM_PROVIDER', ''),
    )
    RAWFISH_LLM_API_KEY = os.environ.get(
        'RAWFISH_LLM_API_KEY',
        os.environ.get('LLM_API_KEY'),
    )
    RAWFISH_LLM_READINESS_TIMEOUT_SEC = float(
        os.environ.get('RAWFISH_LLM_READINESS_TIMEOUT_SEC', '10')
    )
    RAWFISH_LLM_GENERATION_TIMEOUT_SEC = float(
        os.environ.get('RAWFISH_LLM_GENERATION_TIMEOUT_SEC', '60')
    )
    RAWFISH_LLM_MAX_RETRIES = int(
        os.environ.get('RAWFISH_LLM_MAX_RETRIES', '1')
    )
    LLM_API_KEY = RAWFISH_LLM_API_KEY
    LLM_BASE_URL = RAWFISH_LLM_BASE_URL
    LLM_MODEL_NAME = RAWFISH_LLM_MODEL

    # Zep configuration
    # ZEP_MODE controls the audience-store seam backend: the deterministic
    # in-memory mock (``mock``, default), the local Graphiti-backed store
    # (``graphiti``), or the deprecated cloud Zep client
    # (``experimental_cloud``). The 3-mode ladder is parsed in
    # ``app.utils.runtime_env.parse_zep_mode``.
    # ZEP_API_KEY is required ONLY when ZEP_MODE=experimental_cloud; in
    # mock / graphiti mode the key is unused (validate() enforces this).
    # Reads the env var at class-definition time so importing Config does
    # not implicitly require the key.
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    # Class-def-time initialisation MUST NEVER raise on a bogus ZEP_MODE
    # (the boot matrix expects validate() to surface the error, not module
    # import). Swallow InvalidZepMode and fall through to the conservative
    # 'mock' default; validate() is the single boundary that surfaces the
    # error via the SSoT parser.
    try:
        ZEP_MODE = parse_zep_mode()
    except InvalidZepMode:
        ZEP_MODE = 'mock'

    # File upload configuration
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    # Text processing configuration
    DEFAULT_CHUNK_SIZE = 500  # Default chunk size
    DEFAULT_CHUNK_OVERLAP = 50  # Default overlap size

    # OASIS simulation configuration
    # OASIS_DEFAULT_MAX_ROUNDS: default upper bound on rounds applied when the client does not pass max_rounds explicitly.
    # Consumed by SimulationRunner. The default of 150 covers typical configurations (72h/30min = 144 rounds) to avoid
    # silently truncating standard demos; longer configurations are capped to this value, and in any case they cannot
    # exceed the hard cap OASIS_MAX_ROUNDS_CAP. Anonymous denial-of-wallet is already blocked by API-key auth; this only
    # bounds the per-run cost ceiling for "authenticated clients".
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '150'))
    # Hard caps (denial-of-wallet protection): no matter what the client passes in, rounds/agent counts must not exceed these caps.
    OASIS_MAX_ROUNDS_CAP = int(os.environ.get('OASIS_MAX_ROUNDS_CAP', '200'))
    OASIS_MAX_AGENTS_CAP = int(os.environ.get('OASIS_MAX_AGENTS_CAP', '1000'))
    # Simulation timeouts (seconds): per-round env.step timeout + overall simulation timeout. Subprocesses read environment variables with the same names.
    OASIS_ROUND_TIMEOUT_SEC = int(os.environ.get('OASIS_ROUND_TIMEOUT_SEC', '600'))
    OASIS_RUN_TIMEOUT_SEC = int(os.environ.get('OASIS_RUN_TIMEOUT_SEC', '7200'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')

    # Available actions on the OASIS platform
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]

    # Report Agent configuration
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    # Expose whether SECRET_KEY came from env or the per-start ephemeral
    # fallback so validate() can FAIL (not just warn) in production with a
    # missing or placeholder key.
    SECRET_KEY_EPHEMERAL = (_SECRET_KEY_FROM_ENV is None)

    @classmethod
    def validate(cls) -> list[str]:
        """Validate required configuration. Returns a list of errors; empty list = OK."""
        errors: list[str] = []
        try:
            from .simulation_platform.llm.router import (
                check_provider_readiness,
                provider_config_from_env,
            )

            provider_config = provider_config_from_env(include_secret=True)
            cls.RAWFISH_LLM_PROVIDER_MODE = provider_config.mode
            cls.RAWFISH_LLM_MODEL = provider_config.model
            cls.RAWFISH_LLM_BASE_URL = provider_config.base_url
            cls.RAWFISH_LLM_PROVIDER = provider_config.provider or ""
            cls.RAWFISH_LLM_API_KEY = provider_config.api_key
            cls.LLM_API_KEY = provider_config.api_key
            cls.LLM_BASE_URL = provider_config.base_url
            cls.LLM_MODEL_NAME = provider_config.model
            provider_readiness = check_provider_readiness(
                provider_config,
                probe=False,
            )
            if not provider_readiness.configured:
                errors.append(provider_readiness.reason or provider_readiness.status)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"RAWFISH LLM provider configuration is invalid: {exc}")

        # ZEP_MODE=experimental_cloud with no ZEP_API_KEY MUST BOOT
        # successfully (defense-in-depth at the endpoint boundary, not
        # fail-fast at boot). validate() therefore only rejects truly
        # invalid ZEP_MODE values; request-time provider checks surface
        # missing credentials through the RawFish API envelope.
        #
        # Re-read from env at validate() time so test monkeypatches after
        # import-time take effect. parse_zep_mode is the SSoT (loaded at
        # module top via importlib bypass; see header). Catch InvalidZepMode
        # and forward to the error list rather than raising -- callers
        # (create_app) decide whether to fail boot.
        try:
            zep_mode = parse_zep_mode()
            cls.ZEP_MODE = zep_mode
        except InvalidZepMode as exc:
            # Preserve the existing error text ("Invalid ZEP_MODE='production'; ...")
            # by reading the offending value off the exception attribute --
            # avoids re-reading os.environ from outside the SSoT.
            offending = (exc.raw_value if exc.raw_value is not None else 'mock')
            cls.ZEP_MODE = (
                offending.strip().lower()
                if isinstance(offending, str) else 'mock'
            )
            errors.append(
                f"Invalid ZEP_MODE={cls.ZEP_MODE!r}; expected one of "
                f"'mock', 'graphiti', 'experimental_cloud'"
            )

        # Hard-fail in production for missing or placeholder SECRET_KEY
        # values. An only-warn behaviour here would leave the door open to
        # ephemeral keys (sessions break across restarts; the Werkzeug
        # debugger PIN derivation weakens against a known seed).
        if not cls.DEBUG:
            placeholder_secrets = {
                'rawfish-secret-key',
                'change_me_to_a_random_secret',
                'your_random_secret',
                'change_me',
                'changeme',
                'dev-only-secret-32-chars-or-more',  # the doc sample for boot-only smoke
                'test-secret-32chars-test-secret-32',  # the pytest fixture default
            }
            if cls.SECRET_KEY_EPHEMERAL:
                errors.append(
                    "Production mode (FLASK_DEBUG=false) requires the SECRET_KEY environment "
                    "variable to be set explicitly; got an ephemeral random key (would break "
                    "sessions across restarts and weaken Werkzeug debugger PIN)."
                )
            elif cls.SECRET_KEY in placeholder_secrets:
                errors.append(
                    f"Production mode (FLASK_DEBUG=false) requires a non-placeholder SECRET_KEY; "
                    f"got a known placeholder value. Generate one with: "
                    f"python -c 'import secrets; print(secrets.token_hex(32))'"
                )
            elif len(cls.SECRET_KEY) < 32:
                errors.append(
                    f"Production mode SECRET_KEY must be at least 32 characters "
                    f"(got {len(cls.SECRET_KEY)}). Generate one with: "
                    f"python -c 'import secrets; print(secrets.token_hex(32))'"
                )

        # When auth is enabled, API_KEY must be configured, otherwise every /api/* will return 401
        if cls.AUTH_ENABLED and not cls.API_KEY:
            errors.append("When AUTH_ENABLED=true, API_KEY must be set (or explicitly disable auth with AUTH_ENABLED=false)")

        # When auth is enabled in production (FLASK_DEBUG=false), reject
        # known .env.example placeholder API_KEY values. The unset-key check
        # above only rejects an UNSET key; an operator who copied .env.example
        # verbatim and never edited it would otherwise boot with a key any
        # reader of the repo could lift directly from version control. Match
        # known sentinel strings AND template-prefix patterns ("change_me…",
        # "your_…") case-insensitively so this catches near-misses, not just
        # the exact strings shipped in .env.example.
        if cls.AUTH_ENABLED and not cls.DEBUG and cls.API_KEY:
            api_key_normalized = cls.API_KEY.strip().lower()
            placeholder_api_keys = {
                "change_me_to_a_strong_api_key",
                "your_api_key_here",
                "test-api-key",
            }
            placeholder_prefixes = ("change_me", "your_")
            is_placeholder = (
                api_key_normalized in placeholder_api_keys
                or api_key_normalized.startswith(placeholder_prefixes)
            )
            if is_placeholder:
                errors.append(
                    "API_KEY appears to be a template placeholder; set a real "
                    "value before enabling auth in production"
                )
            # VITE_API_KEY mirrors API_KEY into the frontend bundle. The
            # bundled UI uses it as its sole authentication credential
            # against ``/api/*``; an empty or placeholder value causes
            # every UI request to 401 with no operator-visible signal at
            # boot. Reject both shapes so the failure surfaces as a
            # validation error before Gunicorn binds, not as an opaque
            # 401 wave at runtime.
            #
            # Read from env (Config does not expose VITE_API_KEY as a
            # class attribute because the backend doesn't otherwise
            # consume it -- it is a build-time bake into the frontend
            # bundle).
            vite_api_key_raw = (os.environ.get("VITE_API_KEY") or "").strip()
            vite_api_key = vite_api_key_raw.lower()
            if not vite_api_key:
                errors.append(
                    "Production mode (FLASK_DEBUG=false) with AUTH_ENABLED=true "
                    "requires VITE_API_KEY to be set; without it the bundled "
                    "frontend cannot authenticate against /api/* and every UI "
                    "request returns 401. Set VITE_API_KEY to the same value "
                    "as API_KEY (typically baked into the frontend bundle at "
                    "build time via `docker compose build` or `npm run build`)."
                )
            elif (
                vite_api_key in placeholder_api_keys
                or vite_api_key.startswith(placeholder_prefixes)
            ):
                errors.append(
                    "VITE_API_KEY appears to be a template placeholder; set a "
                    "real value before enabling auth in production"
                )
        return errors
