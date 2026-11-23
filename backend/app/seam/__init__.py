"""
Policy seam package.

This package houses subclass-only overrides that harden the base clients
and readers without modifying the modules under ``backend/app/utils/``. The
seam isolates policy-path patches (LLM JSON repair, timeouts,
delimiter-injection rejection, etc.) from those base modules.

Rule: never patch the base modules. Always subclass and override here.
"""
