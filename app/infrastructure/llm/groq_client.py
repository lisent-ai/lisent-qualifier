"""
Groq streaming client using openai/gpt-oss-120b.
Circuit breaker + async generator for SSE streaming.
"""
import json
import structlog
from typing import Any, AsyncGenerator

from circuitbreaker import circuit
from groq import AsyncGroq, APIStatusError, APITimeoutError

from app.config import get_settings

log = structlog.get_logger(__name__)


# Tools surfaced to the chat LLM. The model decides when to call them;
# the dispatcher in stream_chat_with_tools runs the call and threads the
# result back into the conversation before resuming the stream.
CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "Search the company's knowledge base (projects, units, "
                "prices, locations, specs, policies). Call this whenever "
                "the user asks about concrete company offerings — project "
                "names, available units, pricing, amenities, delivery "
                "dates, locations — and the answer is not already in the "
                "conversation. Place names MUST stay in the language the "
                "corpus uses: Turkish (Girne, Lefkoşa, Çatalköy, Esentepe, "
                "İskele), NOT their English exonyms (Kyrenia, Nicosia). "
                "Generic nouns (villa, apartment, project, price) can be "
                "either language; include both when unsure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "2–6 key terms separated by spaces. Turkish "
                            "place names, mixed-language generic terms. "
                            "Examples: 'Girne villa 3 yatak', "
                            "'Esentepe stüdyo price', "
                            "'Phuket resort active'."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]


async def _run_search_knowledge_base(company_id: str, query: str) -> str:
    """Tool dispatcher for ``search_knowledge_base``. Returns a plain-text
    block of the top chunks, ready to paste back into the model as the
    tool-result message. Errors are swallowed and returned as a short
    note so the model can recover on its own."""

    if not company_id or not query:
        return "No query supplied."
    try:
        from app.infrastructure.rag import repository as kb_repo

        hits = await kb_repo.search_chunks(company_id, query, top_k=5)
    except Exception as exc:  # pragma: no cover — guard rail
        log.warning("rag_tool_search_failed", error=str(exc), query=query)
        return f"Search failed: {exc}"
    if not hits:
        return f"No results for '{query}'."
    block = "\n\n---\n\n".join(
        f"[{c.title or c.doc_ref}]\n{c.content}" for c in hits
    )
    return block[:4000]

_groq_client: AsyncGroq | None = None


def get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        settings = get_settings()
        _groq_client = AsyncGroq(
            api_key=settings.groq_api_key,
            timeout=settings.groq_timeout_seconds,
        )
    return _groq_client


async def close_groq_client() -> None:
    global _groq_client
    if _groq_client is not None:
        await _groq_client.close()
        _groq_client = None


@circuit(failure_threshold=3, recovery_timeout=60, expected_exception=(APIStatusError, APITimeoutError, Exception))
async def stream_chat(
    messages: list[dict],
    session_id: str,
) -> AsyncGenerator[str, None]:
    """Yields token strings. Raises on circuit open or Groq error."""
    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=4000, priority="chat"):
        raise RuntimeError("Groq rate limit — chat throttled")

    settings = get_settings()
    client = get_groq_client()

    stream = await client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        max_tokens=settings.groq_max_tokens,
        temperature=0.7,
        stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


@circuit(failure_threshold=3, recovery_timeout=60, expected_exception=(APIStatusError, APITimeoutError, Exception))
async def stream_chat_with_tools(
    messages: list[dict],
    session_id: str,
    company_id: str = "",
    max_tool_iterations: int = 3,
) -> AsyncGenerator[str, None]:
    """Streaming chat completion with tool-calling support.

    Yields only user-facing content tokens. When the model emits
    tool_calls instead of content, we accumulate the deltas, execute
    each tool, append the result as a ``tool`` role message, and start
    a fresh streaming completion — up to ``max_tool_iterations`` times.
    """

    from app.infrastructure.llm.groq_rate_limiter import acquire

    if not await acquire(estimated_tokens=4000, priority="chat"):
        raise RuntimeError("Groq rate limit — chat throttled")

    settings = get_settings()
    client = get_groq_client()

    # messages is mutated in place across tool iterations so the model
    # sees the full tool_call ↔ tool_result chain. The caller's outer
    # array is the same object; that is intentional — callers saving
    # final transcripts only need the visible assistant content.
    convo = list(messages)

    for iteration in range(max_tool_iterations + 1):
        stream = await client.chat.completions.create(
            model=settings.groq_model,
            messages=convo,
            max_tokens=settings.groq_max_tokens,
            temperature=0.7,
            tools=CHAT_TOOLS,
            tool_choice="auto",
            stream=True,
        )

        assistant_content = ""
        tool_calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None

        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                assistant_content += delta.content
                yield delta.content

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    slot = tool_calls.setdefault(
                        tc.index, {"id": "", "name": "", "args": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["args"] += tc.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        if finish_reason != "tool_calls" or not tool_calls:
            return

        if iteration == max_tool_iterations:
            log.warning(
                "tool_loop_exhausted",
                session_id=session_id,
                pending=[v["name"] for v in tool_calls.values()],
            )
            return

        # Append assistant tool_call turn + each tool result.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_content or None,
            "tool_calls": [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {
                        "name": slot["name"],
                        "arguments": slot["args"] or "{}",
                    },
                }
                for slot in tool_calls.values()
            ],
        }
        convo.append(assistant_msg)

        for slot in tool_calls.values():
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if slot["name"] == "search_knowledge_base":
                result = await _run_search_knowledge_base(
                    company_id, str(args.get("query", "")).strip()
                )
            else:
                result = f"Unknown tool: {slot['name']}"
            log.info(
                "chat_tool_call",
                session_id=session_id,
                tool=slot["name"],
                query=args.get("query", "")[:120] if isinstance(args, dict) else "",
                result_len=len(result),
            )
            convo.append(
                {
                    "role": "tool",
                    "tool_call_id": slot["id"],
                    "content": result,
                }
            )


async def complete_chat(messages: list[dict]) -> str:
    """Non-streaming completion for handoff closing message."""
    from app.infrastructure.llm.groq_rate_limiter import acquire
    if not await acquire(estimated_tokens=500, priority="other"):
        return ""  # Skip closing message if rate limited

    settings = get_settings()
    client = get_groq_client()

    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        max_tokens=256,
        temperature=0.5,
        stream=False,
    )
    return response.choices[0].message.content or ""
