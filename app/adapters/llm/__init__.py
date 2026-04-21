"""
LLMPort adapters.

Şu anki kod iki LLM provider'a yaslanıyor; her biri ayrı use case'e (chat vs
structured extraction) optimize. İleride tek adapter her iki role'u da
üstlenebilir (Groq JSON mode, Anthropic structured, vs).

    - GroqAdapter — Groq cloud, streaming chat + tool_use (mevcut groq_client)
    - LocalLlamaAdapter — llama.cpp local, structured extraction + reasoning
      (mevcut local_llm_client)

Gelecek (Phase 6+):
    - OpenAICompatAdapter (xAI Grok, Cloudflare Workers AI)
    - AnthropicAdapter (Claude, native tool use + structured output)
"""

from app.adapters.llm.groq import GroqAdapter
from app.adapters.llm.local_llama import LocalLlamaAdapter

__all__ = ["GroqAdapter", "LocalLlamaAdapter"]
