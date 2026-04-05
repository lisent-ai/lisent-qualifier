# AI Lead Qualifier

## Project Overview

AI-powered lead qualification microservice. Receives leads via webhooks (form submissions, WhatsApp), scores them using a composite scoring engine (rule-based + CHAMP extraction + engagement + sector signals), and routes qualified leads to the CRM.

**Multilingual & Multi-Sector:** Templates, sentiment lexicons, and sector qualifiers are pluggable via registry pattern. Currently supports Turkish + English, construction sector.

**Two qualification paths:**
- **Fast Path** (score ≥ dynamic threshold): Generates reasoning report via local LLM, immediately sends to CRM
- **Chat Path** (score < threshold): Initiates AI conversation via Groq to gather CHAMP data; re-scores on extraction cycles until threshold crossed

## Tech Stack

- **Language:** Python 3.13 (free-threaded/no-GIL)
- **Framework:** FastAPI + Uvicorn (uvloop)
- **Cloud LLM:** Groq API (openai/gpt-oss-120b) — streaming chat
- **Local LLM:** llama.cpp HTTP server (Qwen3.5-4B GGUF) — CHAMP extraction, reasoning reports (structured output via response_format)
- **Database:** PostgreSQL 17 + pgvector (asyncpg)
- **Cache/Queue:** Redis 7.4 (hiredis) — sessions, PubSub, outbox, dedup
- **HTTP Client:** httpx with HTTP/2
- **Resilience:** tenacity (retry), circuitbreaker (circuit breaker)
- **Observability:** structlog (JSON), prometheus_client
- **Build:** hatchling, uv

## Architecture

Layered DDD architecture:

```
app/api/            → FastAPI routers, request/response schemas
app/application/    → Command handlers, orchestration (lead_intake, conversation, qualification, scoring, whatsapp)
app/domain/         → Pure business logic, entities, scoring rules (no I/O)
  domain/scoring/
    ├── champ.py, composite_scorer.py, scorer.py, thresholds.py, engagement.py, events.py
    ├── signals/          → Language/sector-specific signal analyzers
    │   ├── base.py, registry.py
    │   ├── languages/    → tr.py, en.py (sentiment, intent, buying signals)
    │   └── sectors/      → construction.py, general.py (negative signals, seasonality)
    └── qualifiers/       → construction.py, general.py (sector-specific bonus scoring)
  domain/conversation/
    ├── prompts.py        → Generic prompt builder with gap-aware CHAMP routing
    ├── templates/        → Language-specific prompt templates (tr/, en/)
    └── few_shots/        → Language+sector few-shot examples
app/infrastructure/ → External service clients (LLM, DB, Redis, CRM, GreenAPI)
```

**Key patterns:** Command pattern, Repository pattern, Circuit Breaker, Retry with backoff, Redis PubSub, Dead-letter outbox, Registry pattern (multilingual/multi-sector).

## Scoring

### Composite Scoring (replaces old max(rule, bant))
```
final = fit*0.30 + qualification*0.45 + engagement*0.15 + sector_bonus*0.10
      + negative_adjustments + seasonal_modifier
```

- **Fit Score** (0-100): Rule-based from form data — Budget (30pts), Timeline (25pts), Project Type (20pts), Authority (15pts), Data Quality (10pts)
- **Qualification Score** (0-100): CHAMP extraction — Challenges (0-25), Authority (0-25), Money (0-25), Prioritization (0-25)
- **Engagement Score** (0-100): Response speed, message substance, question frequency, conversation depth
- **Sector Bonus** (0-10): Construction-specific: land ownership, permit status, architect, budget source
- **Negative Signals**: Price fishing, just-looking, inactivity decay, competitor detection
- **Seasonal Modifier** (-5 to +5): Construction seasonality

### Dynamic Threshold
Threshold varies by project type + budget (commercial/10M+ = 65, renovation/500K- = 85, default = 75). Configurable per company via `qualification_threshold`.

### CHAMP Framework (Challenges, Authority, Money, Prioritization)
- Per-dimension confidence tracking (0.0-1.0)
- Monotonic scoring (scores never decrease)
- Few-shot examples for calibrated extraction
- Gap-aware question routing (tells chat LLM which dimension to probe)

## Key Flows

1. **Webhook Intake:** `POST /webhook/lead/{company_token}` → score → fast path or chat path
2. **Chat:** `POST /chat/message/{session_id}` + `GET /chat/stream/{session_id}` (SSE) → CHAMP extraction every N messages → handoff when composite score ≥ threshold
3. **WhatsApp:** `POST /webhook/whatsapp` (GreenAPI) → dedup → create CRM customer/lead → route to conversation
4. **Score Stream:** `GET /chat/score-stream/{session_id}` (SSE via Redis PubSub)

## API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /health`, `GET /ready` | Health & readiness probes |
| `GET /metrics` | Prometheus metrics |
| `POST /webhook/lead/{company_token}` | Lead intake from forms |
| `POST /chat/message/{session_id}` | Send user message |
| `GET /chat/stream/{session_id}` | Stream AI response (SSE) |
| `GET /chat/score-stream/{session_id}` | Stream score updates (SSE) |
| `POST /webhook/whatsapp` | GreenAPI incoming messages |
| `GET /internal/leads/{company_id}` | Internal lead listing (API key auth) |

## Database

**PostgreSQL tables:** `qualifier_leads`, `qualifier_sessions`, `qualifier_handoffs`
**Redis keys:** `session:{id}` (HASH), `scores:{id}` (ZSET), `lock:champ:{id}`, `crm:outbox` (LIST), `dedup:{id}`, `phone_session:{company}:{phone}`

## Development

```bash
# Install
uv pip install -e ".[dev]"

# Run services
docker-compose up -d

# Run API
uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop uvloop

# Start local LLM
./scripts/start_local_llm.sh

# Test
pytest --cov=app tests/

# Lint
ruff check .
```

## Configuration

All config via environment variables (see `.env.example`). Key settings:
- `HIGH_THRESHOLD=80` — default threshold (overridden by dynamic compute_threshold)
- `CHAMP_EXTRACT_EVERY_N_MESSAGES=3` — extraction frequency
- `GROQ_API_KEY`, `GROQ_MODEL` — cloud LLM
- `LOCAL_LLM_URL` — llama.cpp server (default: `http://host.docker.internal:8080`)
- `CRM_WEBHOOK_URL`, `CRM_API_KEY` — CRM integration
- `DATABASE_URL` — PostgreSQL connection
- `REDIS_DSN` — Redis connection

## External Dependencies

| Service | Purpose |
|---------|---------|
| Groq API | Streaming chat responses |
| llama.cpp (local) | CHAMP extraction, reasoning reports |
| PostgreSQL 17 | Lead/session persistence |
| Redis 7.4 | Session state, PubSub, outbox |
| CRM Service | Company lookup, customer/lead creation, webhook handoff |
| GreenAPI | WhatsApp messaging |

## Code Style

- Line length: 100 (ruff)
- Target: Python 3.13
- Linting rules: E, F, I, UP
- Async/await throughout — no blocking I/O
- Multilingual prompts via template registry (primary: Turkish, secondary: English)
