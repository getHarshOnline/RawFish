"""Compatibility LLM client facade backed by the RawFish provider router."""

import os
from typing import Optional, Dict, Any, List

from ..config import Config
from ..simulation_platform.llm.router import (
    RawFishLiteLLMClient,
    provider_config_from_env,
)


class LLMClient:
    """Small compatibility facade over the LiteLLM provider router."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.config = provider_config_from_env()
        config_fallbacks = {
            key: value
            for key, value in {
                "base_url": Config.LLM_BASE_URL,
                "model": Config.LLM_MODEL_NAME,
            }.items()
            if value not in (None, "")
        }
        if (
            "base_url" in config_fallbacks
            and (
                os.environ.get("RAWFISH_LLM_BASE_URL")
                or os.environ.get("LLM_BASE_URL")
            )
        ):
            config_fallbacks.pop("base_url", None)
        if (
            "model" in config_fallbacks
            and (
                os.environ.get("RAWFISH_LLM_MODEL")
                or os.environ.get("LLM_MODEL_NAME")
            )
        ):
            config_fallbacks.pop("model", None)
        if config_fallbacks:
            self.config = self.config.model_copy(update=config_fallbacks)
        overrides = {
            key: value
            for key, value in {
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
            }.items()
            if value not in (None, "")
        }
        if overrides:
            self.config = self.config.model_copy(update=overrides)
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url
        self.model = self.config.model
        self.client = RawFishLiteLLMClient(self.config)
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        Send a chat request.

        Args:
            messages: List of messages
            temperature: Temperature parameter
            max_tokens: Maximum number of tokens
            response_format: Response format (e.g. JSON mode)

        Returns:
            Model response text
        """
        return self.client.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        Send a chat request and return the response as JSON.

        Args:
            messages: List of messages
            temperature: Temperature parameter
            max_tokens: Maximum number of tokens

        Returns:
            Parsed JSON object
        """
        return self.client.chat_json(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
