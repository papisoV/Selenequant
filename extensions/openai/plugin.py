"""OpenAI extension plugin entrypoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from selene_space_ext_openai.provider import OpenAIExtensionProvider

from selene_space_core.plugin_sdk.api import SeleneSpacePluginAPI

_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.json"
MANIFEST: dict[str, Any] = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def setup(api: SeleneSpacePluginAPI) -> None:
    """Register the OpenAI-compatible LLM provider."""
    api.register_llm_provider("openai", OpenAIExtensionProvider)
