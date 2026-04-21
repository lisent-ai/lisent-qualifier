"""
LocalLlamaAdapter — llama.cpp local LLM'i LLMPort'a uydurur.

`app.infrastructure.llm.local_llm_client` modülünü wrap eder. Structured
extraction (CHAMP, reasoning reports) native olarak destekler — response_format
directive ile JSON schema'ya uyan output üretir.

Phase 1.D.3:
    - structured_extract() — _call_local_llm() low-level fonksiyonu kullanır
    - stream_chat() — NotImplementedError (llama.cpp streaming desteği var ama
      mevcut kod kullanmıyor; Phase 6'da eklenebilir)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import structlog

from app.infrastructure.llm import local_llm_client
from app.ports.llm import ChatMessage, LLMPort

log = structlog.get_logger(__name__)


class LocalLlamaAdapter(LLMPort):
    """LLMPort over llama.cpp local server (structured output)."""

    @property
    def name(self) -> str:
        return "local_llama"

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        tenant_id: Any | None = None,
    ) -> AsyncGenerator[str, None]:
        """Phase 6+: llama.cpp streaming endpoint'i (SSE over /v1/chat/completions
        with stream=true). Şu an chat için GroqAdapter tercih ediliyor (latency)."""
        raise NotImplementedError(
            "LocalLlamaAdapter.stream_chat Phase 6+'da eklenecek. "
            "Chat için GroqAdapter kullanın."
        )
        # Python syntax zorla async generator
        if False:  # pragma: no cover
            yield ""

    async def structured_extract(
        self,
        messages: list[ChatMessage],
        json_schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.05,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """llama.cpp /v1/chat/completions + response_format=json_schema.

        Underlying `_call_local_llm` döndürdüğü raw string'i JSON parse ederiz.
        Response_format schema uyumu llama.cpp server tarafında enforce edilir
        (Grammar-constrained decoding).
        """
        import json

        dict_messages = [self._chat_message_to_dict(m) for m in messages]

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": json_schema.get("name", "StructuredOutput"),
                "strict": True,
                "schema": json_schema.get("schema", json_schema),
            },
        }

        raw = await local_llm_client._call_local_llm(
            messages=dict_messages,
            timeout=timeout_seconds,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        # Local LLM bazen prose öncesi/sonrası bıraksın diye _extract_json wrapper
        # Şu an _call_local_llm zaten ham JSON dönüyor; tutarlılık için explicit parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: prose içinden JSON çıkart
            cleaned = local_llm_client._extract_json(raw)
            return json.loads(cleaned)

    # ------------------------------------------------------------------

    @staticmethod
    def _chat_message_to_dict(m: ChatMessage) -> dict[str, Any]:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        return d
