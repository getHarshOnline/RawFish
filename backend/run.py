"""
RawFish Backend startup entry point
"""

import os
import sys

# Fix garbled non-ASCII output in the Windows console by setting UTF-8 encoding before any imports
if sys.platform == 'win32':
    # Set environment variable to ensure Python uses UTF-8
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    # Reconfigure standard output streams to UTF-8
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add the project root directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _debug_enabled_from_env() -> bool:
    return os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'


def _refuse_nonloopback_debug(host: str, debug: bool) -> None:
    if debug and host not in ('127.0.0.1', 'localhost', '::1'):
        print(
            f"REFUSED: FLASK_DEBUG=true with FLASK_HOST={host!r} would expose "
            "the Werkzeug interactive debugger (remote code execution surface) "
            "to a non-loopback interface. Either set FLASK_DEBUG=false for "
            f"FLASK_HOST={host!r}, or restrict FLASK_HOST to 127.0.0.1/localhost.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)


# Refuse the unsafe debugger bind before importing the app package. Some app
# imports can start background resources; the safety failure must be a clean
# process exit, not a printed warning followed by a hung interpreter.
_early_host = os.environ.get('FLASK_HOST', '127.0.0.1')
_refuse_nonloopback_debug(_early_host, _debug_enabled_from_env())

from app import create_app
from app.config import Config


def main():
    """Main function"""
    # Validate configuration
    errors = Config.validate()
    if errors:
        print("Configuration error:")
        for err in errors:
            print(f"  - {err}")
        print("\nPlease check the configuration in the .env file")
        sys.exit(1)

    # Create the application
    app = create_app()

    # Get runtime configuration.
    # Refuse to bind FLASK_HOST=0.0.0.0 (or any non-loopback)
    # while FLASK_DEBUG=true. The Werkzeug interactive debugger is a remote-RCE
    # surface; exposing it to all interfaces was historically the root cause of
    # CVE-class incidents.
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG

    _refuse_nonloopback_debug(host, debug)

    if not debug and host in ('127.0.0.1', 'localhost', '::1'):
        # Production-mode running on loopback is unusual but not unsafe; warn only.
        print(
            f"NOTE: FLASK_DEBUG=false with FLASK_HOST={host!r} -- production "
            "mode binding to loopback only. For real deployment, set "
            "FLASK_HOST=0.0.0.0 (behind a reverse proxy with TLS).",
            file=sys.stderr,
        )

    # Start the service
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    main()
