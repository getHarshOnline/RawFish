"""Lane-neutral LLM provider/factory seam."""
from __future__ import annotations

from typing import Any, Optional, Protocol, cast, runtime_checkable

from .protocols import LLMClientProtocol
from .router import ProviderConfig, RawFishLLMProviderFactory


class RunScopedProviderConfigRequired(RuntimeError):
    """Raised when a lane-owned factory cannot consume ProviderConfig."""


@runtime_checkable
class LLMProviderFactory(Protocol):
    """Structural contract for constructing an LLM client.

    The platform route + watcher invoke :meth:`build_client` once
    per request / per generation cycle. Run-scoped factories can also
    expose ``build_client_for_config`` to receive the persisted
    RawFish provider/model binding for that run.

    Conformance is structural; a free function ``build_client()``
    wrapped in a class is sufficient. The Protocol exists so test
    fixtures can substitute an in-memory client without needing to
    inherit from a concrete base.
    """

    def build_client(self) -> LLMClientProtocol:
        ...


@runtime_checkable
class RunScopedLLMProviderFactory(LLMProviderFactory, Protocol):
    """Factory that can bind an explicit per-run provider config."""

    def build_client_for_config(
        self,
        config: ProviderConfig,
    ) -> LLMClientProtocol:
        ...


DEFAULT_LLM_PROVIDER_FACTORY: LLMProviderFactory = RawFishLLMProviderFactory()


# Process-wide override slot. Tests rebind this to swap in a fake
# factory without monkeypatching the platform's import sites; the
# binding lasts the test's lifetime and is reset to ``None`` after.
# Production never sets this.
_OVERRIDE_FACTORY: Optional[LLMProviderFactory] = None


def get_llm_provider_factory(
    adapter: object = None,
) -> LLMProviderFactory:
    """Resolve the LLM provider factory for a given adapter.

    Precedence (first non-None wins):

      1. :data:`_OVERRIDE_FACTORY` -- test-set process-wide override.
      2. ``adapter.llm_provider_factory`` -- lane-specific override
         when the adapter declares one.
      3. :data:`DEFAULT_LLM_PROVIDER_FACTORY` -- the base factory.

    ``adapter`` may be ``None`` when the caller has no adapter
    context (e.g. the auto-report watcher's pre-resolve guard); the
    function still returns a usable factory because levels 1 and 3
    do not depend on the adapter.
    """
    if _OVERRIDE_FACTORY is not None:
        return _OVERRIDE_FACTORY
    if adapter is not None:
        candidate = getattr(adapter, "llm_provider_factory", None)
        if candidate is not None:
            return cast(LLMProviderFactory, candidate)
    return DEFAULT_LLM_PROVIDER_FACTORY


def build_llm_client_for_run(
    *,
    adapter: object = None,
    provider_config: Optional[ProviderConfig] = None,
) -> LLMClientProtocol:
    """Construct an LLM client for a report-generation run.

    Adapter-specific factories still win, but lane-owned factories must
    implement ``build_client_for_config`` when a persisted per-run
    binding is available. Falling back to ``build_client`` for those
    factories would silently ignore the run's provider/model choice.
    The default RawFish factory remains usable in both modes, and
    legacy/test callers with no adapter-owned custom factory can still
    rely on ``build_client``.
    """
    factory = get_llm_provider_factory(adapter)
    if provider_config is not None:
        build_for_config = getattr(factory, "build_client_for_config", None)
        if callable(build_for_config):
            return cast(LLMClientProtocol, build_for_config(provider_config))
        adapter_factory = (
            getattr(adapter, "llm_provider_factory", None)
            if adapter is not None else None
        )
        if adapter_factory is factory:
            raise RunScopedProviderConfigRequired(
                "adapter llm_provider_factory must implement "
                "build_client_for_config when a run-scoped ProviderConfig "
                "is present"
            )
        if not isinstance(factory, RawFishLLMProviderFactory):
            raise RunScopedProviderConfigRequired(
                "LLM provider factory must implement build_client_for_config "
                "when a run-scoped ProviderConfig is present"
            )
    return cast(LLMClientProtocol, factory.build_client())


def set_override_factory(factory: Optional[Any]) -> None:
    """Set / clear the process-wide override.

    Pass a factory to swap in a fake; pass ``None`` to clear. The
    rebind is process-wide and is intended for test fixtures only.
    """
    global _OVERRIDE_FACTORY
    _OVERRIDE_FACTORY = cast(Optional[LLMProviderFactory], factory)


__all__ = [
    "LLMProviderFactory",
    "RunScopedProviderConfigRequired",
    "RunScopedLLMProviderFactory",
    "DEFAULT_LLM_PROVIDER_FACTORY",
    "build_llm_client_for_run",
    "get_llm_provider_factory",
    "set_override_factory",
]
