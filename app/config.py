from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # Scoring
    high_threshold: int = Field(default=80, ge=0, le=100)
    champ_extract_every_n_messages: int = Field(default=3, ge=1)

    # Redis
    redis_dsn: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = Field(default=86400, ge=60)

    # Groq
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_timeout_seconds: int = 5
    groq_max_tokens: int = 4096

    # Local LLM
    local_llm_url: str = "http://host.docker.internal:8080"
    local_llm_model: str = "qwen3.5-9b"   # override to "qwen3:4b" for Ollama dev
    local_llm_timeout_champ: int = 60
    local_llm_timeout_reasoning: int = 45

    # CRM Webhook (lead handoff)
    crm_webhook_url: str = ""
    crm_webhook_token: str = ""
    crm_webhook_timeout: int = 10

    # CRM REST API (WhatsApp kanalı — customer/lead oluşturma + instance lookup)
    crm_base_url: str = ""
    crm_api_key: str = ""

    # Internal API key (leads listing endpoint için)
    internal_api_key: str = ""

    # Phase 2.A — API key pepper (SHA-256 salt for tenant_api_keys hashing).
    # Production'da env var ile override edilmeli; dev default rotate et.
    api_key_pepper: str = "dev_pepper_rotate_in_prod"

    # Phase 2.O.1 — BFF proxy server-side auth (crm-web → qualifier admin API).
    # Yalnızca BFF proxy bilir; her request'te `X-Lisent-Tenant-Id` header ile
    # hangi tenant'ı temsil ettiği bildirilir. Empty string → devre dışı.
    platform_admin_token: str = ""

    # PostgreSQL (ai-lead-qualifier kendi instance'ı)
    database_url: str = "postgresql://app:qualifierpass@db:5432/lead_qualifier"

    # Qualification Judge
    qualification_judge_model: str = "llama-3.3-70b-versatile"
    qualification_judge_timeout: int = 30
    qualification_judge_max_tokens: int = 8192
    judge_borderline_low: int = 40
    judge_borderline_high: int = 70
    judge_self_consistency_passes: int = 3
    judge_fallback_to_champ: bool = True

    # Pre-Score Judge (Phase 3 — intake'te çalışan ensemble judge)
    pre_score_judge_max_tokens: int = 2048
    pre_score_judge_timeout: int = 12
    pre_score_ensemble_personas: list[str] = Field(
        default_factory=lambda: ["skeptic", "neutral", "opportunity"],
    )
    pre_score_divergence_threshold: int = 20
    # Master switch: app lifespan starts the worker only when true.
    # Integration tests and smoke envs default-off to avoid unintended
    # Groq traffic; enable explicitly in prod via env var.
    prescore_worker_enabled: bool = False

    # Post-Phase 7 — gate threshold-driven routing side effects. When False
    # (the current default) pre-scoring computes the score + breakdown + sales
    # context but does NOT push ai_status or ai_path to the CRM, so sales sees
    # the score without the lead getting auto-bucketed into "qualified" / "new"
    # or routed down a fast/chat path. Flip to True to restore the previous
    # auto-routing behavior.
    prescore_apply_status_routing: bool = False

    # Phase 7.B — inbound CRM webhook listener (replaces REST PATCH of ai-metadata).
    # CRM side exposes POST /internal/webhooks/qualifier/pre-score and verifies
    # HMAC-SHA256 over `{timestamp_ms}.{body}` with the shared secret. When the
    # listener flag is True we fan out `pre_score.judged` events to the URL;
    # when the REST flag is True we ALSO PATCH ai-metadata via REST (dual-write).
    # Rollout plan: listener on → observe 1 week → flip REST off.
    crm_internal_webhook_url: str = ""
    crm_internal_webhook_secret: str = ""
    crm_webhook_listener_enabled: bool = False
    prescore_crm_rest_enabled: bool = True
    crm_internal_webhook_timeout: float = 5.0

    # OSINT (Phase 3)
    osint_enabled: bool = False
    osint_phoneinfoga_url: str = "http://phoneinfoga:5000"
    osint_holehe_url: str = "http://holehe-api:8000"
    osint_profile_stale_days: int = 60
    osint_request_timeout_s: float = 15.0
    osint_holehe_timeout_per_module: float = 4.0

    # Smart extraction
    smart_extraction_enabled: bool = True
    signal_trigger_min_message_length: int = 15
    score_floor_multiplier: float = 0.6
    score_min_floor: int = 30
    score_max_decrease_per_extraction: int = 10
    engagement_decay_start_minutes: int = 15
    negative_signal_weight: float = 0.5  # asymmetric: negatives count half

    # GreenAPI / WhatsApp
    greenapi_base_url: str = "https://api.green-api.com"
    greenapi_reply_timeout: int = Field(default=8, ge=1)
    whatsapp_webhook_token: str = ""
    whatsapp_qualified_message: str = (
        "Teşekkürler! Uzman ekibimiz en kısa sürede sizinle iletişime geçecek."
    )

    # Feature flags
    qualifier_crm_writethrough_enabled: bool = Field(default=False)
    qualifier_legacy_api_enabled: bool = Field(default=True)
    rag_webhook_enabled: bool = Field(default=False)

    # CTA routing
    default_calendly_url: str = "https://calendly.com/redif"
    cta_medium_floor: int = Field(default=50, ge=0, le=100)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
