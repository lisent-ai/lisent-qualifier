"""
GroqAdapter — Groq cloud LLM'i LLMPort'a uydurur.

`app.infrastructure.llm.groq_client` modülünü wrap eder. Streaming chat ve
tool_use zaten mevcut; structured_extract Phase 2+ (Groq'un response_format
JSON mode'u ile) eklenecek.

Phase 1.D.3:
    - stream_chat() — mevcut stream_chat_with_tools() veya stream_chat() çağırır
    - structured_extract() — NotImplementedError (Phase 2+)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import structlog

from app.infrastructure.llm import groq_client
from app.ports.llm import ChatMessage, LLMPort

log = structlog.get_logger(__name__)


class GroqAdapter(LLMPort):
    """LLMPort over Groq cloud (streaming chat + optional tool_use)."""

    @property
    def name(self) -> str:
        return "groq"

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        tenant_id: Any | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming chat — Groq'un ``AsyncGroq.chat.completions.create(stream=True)``
        çağrısına delege eder.

        Mevcut `stream_chat_with_tools` tool_use handling yapar; tools boşsa
        daha basit `stream_chat` fonksiyonu çağrılır.
        """
        dict_messages = [self._chat_message_to_dict(m) for m in messages]

        if tools:
            # Tool-use varyantı — tenant_id gerekli (search_knowledge_base tool'u için)
            if tenant_id is None:
                log.warning(
                    "groq_stream_chat_tools_without_tenant",
                    msg="tenant_id verilmedi; search_knowledge_base tool'u çalışmayabilir",
                )
            async for chunk in groq_client.stream_chat_with_tools(
                session_id=str(tenant_id) if tenant_id else "",
                messages=dict_messages,
                tools=tools,
                max_tokens=max_tokens,
            ):
                yield chunk
        else:
            # Tool'suz basit varyant
            async for chunk in groq_client.stream_chat(
                messages=dict_messages,
                max_tokens=max_tokens,
            ):
                yield chunk

    async def structured_extract(
        self,
        messages: list[ChatMessage],
        json_schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.05,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """Phase 2+: Groq'un ``response_format={"type":"json_schema",...}`` JSON
        mode'u kullanılacak. Şu anda CHAMP extraction + reasoning için
        LocalLlamaAdapter tercih ediliyor (maliyet + privacy)."""
        raise NotImplementedError(
            "GroqAdapter.structured_extract Phase 2+'da eklenecek. "
            "CHAMP extraction için LocalLlamaAdapter'ı kullanın."
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _chat_message_to_dict(m: ChatMessage) -> dict[str, Any]:
        """ChatMessage dataclass → Groq SDK'nın beklediği dict."""
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        return d
