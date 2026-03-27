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
    bant_extract_every_n_messages: int = Field(default=3, ge=1)

    # Redis
    redis_dsn: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = Field(default=86400, ge=60)

    # Groq
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_timeout_seconds: int = 5
    groq_max_tokens: int = 2048

    # Local LLM
    local_llm_url: str = "http://host.docker.internal:8080"
    local_llm_model: str = "qwen3.5-9b"   # override to "qwen3:4b" for Ollama dev
    local_llm_timeout_bant: int = 60
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

    # PostgreSQL (ai-lead-qualifier kendi instance'ı)
    database_url: str = "postgresql://app:qualifierpass@db:5432/lead_qualifier"

    # GreenAPI / WhatsApp
    greenapi_base_url: str = "https://api.green-api.com"
    greenapi_reply_timeout: int = Field(default=8, ge=1)
    whatsapp_webhook_token: str = ""
    whatsapp_qualified_message: str = (
        "Teşekkürler! Uzman ekibimiz en kısa sürede sizinle iletişime geçecek."
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
