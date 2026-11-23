"""
Security helper functions (error disclosure / upload sniffing / path validation).
- safe_traceback: return stack trace to client only in DEBUG mode; in production, log it to the server only
- validate_id:    validate ids coming in from URLs to prevent them from reaching filesystem sinks (path traversal / unusual characters)
- upload_content_ok: sniff uploaded content by magic bytes to prevent binaries from sneaking in via renamed extensions
"""

import os
import re
import traceback as _traceback

from .logger import get_logger

# Allowed id character set: alphanumerics + underscore + hyphen, length 1-64.
# Excludes '/', '.', '\\' and similar characters usable for path traversal or escaping the intended directory.
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def safe_traceback() -> str:
    """
    The full stack trace is always written to the server log; the stack is only
    returned to the client in DEBUG mode. In production we return a generic message
    to avoid leaking internal paths / stack frames to the client.
    """
    # Lazy import of Config to avoid a potential circular dependency with the config module
    from ..config import Config
    tb = _traceback.format_exc()
    get_logger('rawfish.error').error(tb)
    return tb if Config.DEBUG else 'Internal server error (see server logs)'


def safe_error(e) -> str:
    """
    Client-visible error text: return the exception message in DEBUG mode, and a
    generic message in production. This prevents the exception message itself (e.g. the
    path in a FileNotFoundError, or a config value inside a ValueError) from leaking to
    the client. The full exception is still recorded to the server log via each call
    site's logger.error / safe_traceback.
    """
    from ..config import Config
    return str(e) if Config.DEBUG else 'Internal server error (see server logs)'


def validate_id(value: str, kind: str = 'id') -> str:
    """
    Path validation: reject any id that does not match _ID_RE (including '..', '/', empty
    values), blocking path traversal before the id reaches os.path.join / makedirs / rmtree.
    """
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f'Invalid {kind}: {value!r}')
    return value


def upload_content_ok(file_storage, filename: str) -> bool:
    """
    Upload sniffing: validate the file's header content against its extension so that
    renamed binaries cannot slip through an extension allowlist.
    - pdf: must start with %PDF-
    - txt/md/markdown: header must not contain NUL bytes (a typical binary marker)
    The stream pointer is reset after reading so subsequent saves are not affected.
    """
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    head = file_storage.read(512)
    file_storage.seek(0)
    if ext == 'pdf':
        return head[:5] == b'%PDF-'
    if ext in ('txt', 'md', 'markdown'):
        # Accept UTF-16/UTF-32/UTF-8 text with a BOM (such legitimate text can contain NUL
        # bytes, matching file_parser's multi-encoding support); otherwise, if the header
        # contains a NUL byte, treat the file as binary and reject it.
        if head.startswith((b'\xff\xfe', b'\xfe\xff', b'\xef\xbb\xbf')):
            return True
        return b'\x00' not in head
    return False
