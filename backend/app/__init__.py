"""
RawFish Backend - Flask application factory
"""

import hmac
import os
import warnings

# Suppress multiprocessing resource_tracker warnings (from third-party libs like transformers)
# Must be set before any other imports
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, jsonify, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask application factory function"""
    # Config validation: runs inside the factory so the gunicorn (production) path
    # also enforces validation. run.py (the dev entry point) validates separately; this covers
    # the `gunicorn app:create_app()` path which does not go through run.py.
    config_errors = config_class.validate()
    if config_errors:
        raise RuntimeError("Configuration error, cannot start:\n  - " + "\n  - ".join(config_errors))

    app = Flask(__name__)
    app.config.from_object(config_class)

    # Configure JSON encoding: ensure non-ASCII characters display directly (instead of \uXXXX)
    # Flask >= 2.3 exposes this via DefaultJSONProvider subclassing — the
    # canonical pattern. Older Flask falls back to a runtime attribute set if
    # the new provider API is absent (kept for backward compat with any
    # surviving deploys on Flask <2.3).
    try:
        from flask.json.provider import DefaultJSONProvider

        class _UnicodeJSONProvider(DefaultJSONProvider):
            ensure_ascii = False

        app.json_provider_class = _UnicodeJSONProvider
        app.json = _UnicodeJSONProvider(app)
    except ImportError:  # pragma: no cover — Flask < 2.3 fallback
        if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
            setattr(app.json, 'ensure_ascii', False)

    # Set up logging
    logger = setup_logger('rawfish')

    # Only print startup info in the reloader child process (avoid printing twice in debug mode)
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process

    if should_log_startup:
        logger.info("=" * 50)
        logger.info("RawFish Backend starting...")
        logger.info("=" * 50)

    # Enable CORS: restrict origins to Config.ALLOWED_ORIGINS (defaults to local frontend
    # origin); no longer wildcards '*'.
    CORS(app, resources={r"/api/*": {"origins": Config.ALLOWED_ORIGINS}})

    # API Key authentication: all /api/* endpoints require auth.
    # Clients pass it via `X-API-Key: <key>` or `Authorization: Bearer <key>`.
    # Non-/api paths like /health are exempt; CORS preflight (OPTIONS) is allowed through
    # (browser preflights do not carry custom headers).
    @app.before_request
    def require_api_key():
        if not Config.AUTH_ENABLED:
            return None
        path = request.path or ''
        if not path.startswith('/api/'):
            return None
        if request.method == 'OPTIONS':
            return None
        provided = request.headers.get('X-API-Key', '')
        if not provided:
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                provided = auth_header[7:]
        expected = Config.API_KEY or ''
        if not expected:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        # Constant-time comparison to avoid timing side-channels. Both sides are encoded to
        # bytes — compare_digest raises TypeError on str containing non-ASCII characters;
        # encoding makes any input safe so the auth-deny path never crashes with a 500.
        try:
            ok = hmac.compare_digest(provided.encode('utf-8'), expected.encode('utf-8'))
        except Exception:
            ok = False
        if not ok:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return None


    # Register simulation-process cleanup (ensures all simulation processes are terminated
    # when the server shuts down)
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("Simulation process cleanup handler registered")

    # Register the built-in RawFish lanes with the simulation platform
    # registry. The lane singletons keep registration idempotent across
    # repeated create_app() calls in the same test process.
    from .simulation_platform.builtin_lanes import register_builtin_use_cases
    register_builtin_use_cases()

    # Auth warning: the require_api_key handler above is the SOLE auth surface.
    # The removed check_api_key helper ignored
    # AUTH_ENABLED, had no /api/* scope, no Bearer support, no constant-time
    # compare, and used a different header case. Single source of truth =
    # require_api_key().
    if Config.AUTH_ENABLED and not Config.API_KEY:
        logger.warning(
            "AUTH_ENABLED=true but API_KEY is empty -- every /api/* request "
            "will return 401. Either set API_KEY or set AUTH_ENABLED=false explicitly."
        )
    elif not Config.AUTH_ENABLED:
        logger.warning(
            "AUTH_ENABLED=false -- /api/* endpoints have NO authentication. "
            "Local dev only; never expose to untrusted networks."
        )

    # Request-logging middleware
    @app.before_request
    def log_request():
        logger = get_logger('rawfish.request')
        logger.debug(f"Request: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"Request body: {request.get_json(silent=True)}")

    @app.after_request
    def log_response(response):
        logger = get_logger('rawfish.request')
        logger.debug(f"Response: {response.status_code}")
        return response

    # Security response headers (defense in depth): API responses carry basic security headers.
    # The frontend HTML CSP is provided by index.html's <meta> + vite preview response headers
    # (the backend does not serve HTML directly).
    @app.after_request
    def security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        return response

    # Register the RawFish product API.
    from .api.use_cases import use_cases_bp
    app.register_blueprint(use_cases_bp, url_prefix='/api/sim/use-cases')

    # Auto-report watcher: when AUTO_REPORT_GENERATE=true, daemon threads
    # polls every run directory under OASIS_SIMULATION_DATA_DIR and
    # fires the registered use-case adapter's ``generate_report``
    # path as soon as a run reaches the completed state. The opt-in
    # is intentional -- production deployments that want
    # operator-controlled report generation leave the env unset and
    # the watcher stays inert. is_enabled() reads the env at call time
    # so a test process flipping the var between cases does not need a
    # module reload.
    from .simulation_platform.auto_report_watcher import (
        is_enabled as _auto_report_enabled,
        start_watchers_for_all_lanes as _start_auto_report_watchers,
    )
    if _auto_report_enabled():
        #: One daemon thread per registered lane so a completed run
        #: under any namespaced data directory is picked up, not
        #: just the historical ``policy`` default.
        started = _start_auto_report_watchers()
        if should_log_startup:
            logger.info(
                "Auto-report watchers started "
                "(AUTO_REPORT_GENERATE=true, lanes=%s)",
                [w._use_case_id for w in started],  # type: ignore[attr-defined]
            )
    elif should_log_startup:
        logger.info("Auto-report watcher disabled (AUTO_REPORT_GENERATE unset)")

    # Health check
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'RawFish Backend'}

    if should_log_startup:
        logger.info("RawFish Backend startup complete")
    
    return app
