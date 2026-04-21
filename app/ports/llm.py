"""
LLMPort — "Hangi LLM kullanılsın?"

Chat ve structured extraction için LLM erişimini abstract eder. Mevcut kodda
iki ayrı client var: Groq (chat streaming) ve local llama.cpp (CHAMP extraction
+ reasoning report). Port bunları tek arayüz altında toplar.

Adapters (Phase 1.D):
    - GroqAdapter — mevcut `app/infrastructure/llm/groq_client.py` kodu
      (AsyncGroq + tool_use + circuit breaker)
    - LocalLlamaAdapter — mevcut `app/infrastructure/llm/local_llm_client.py` kodu
      (httpx → llama.cpp /v1/chat/completions + response_format JSON schema)

İleride (Phase 6+):
    - OpenAICompatAdapter — OpenAI, xAI Grok, Cloudflare Workers AI
    - AnthropicAdapter — Claude API direkt
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    name: str | None = None


class LLMPort(ABC):
    """LLM erişimi — chat streaming + structured extraction."""

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        tenant_id: Any | None = None,  # Tool exec context için (search_kb vs)
    ) -> AsyncGenerator[str, None]:
        """Streaming chat tokens yield eder.

        Tools verilirse, adapter tool_use handling yapar (Groq native tool_use
        veya OpenAI-compat function calling). Tool result'ı messages'a append
        ederek devam eder.
        """

    @abstractmethod
    async def structured_extract(
        self,
        messages: list[ChatMessage],
        json_schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.05,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """JSON schema'ya uyan structured output üretir.

        CHAMP extraction + reasoning report için kullanılır. Response'un schema'ya
        uyduğu garanti (LLM native response_format veya post-validation).

        Local llama.cpp için response_format directive kullanılır; Groq için
        tool_use veya prompt engineering + json.loads + validation.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter adı — telemetry / logging için. Örn. 'groq', 'local_llama'."""
