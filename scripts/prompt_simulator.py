#!/usr/bin/env python3
"""
Prompt Simulator — WhatsApp üzerinden AI sohbet akışını TAM SİMÜLE eder.

Production'daki tüm katmanlar aktif:
  - Per-message LLM classification + signal analysis (Groq)
  - Smart CHAMP extraction trigger (information value based)
  - Conversation end detection → force extract (judge karar verir)
  - Max messages → force handoff safety net
  - Qualification Judge (Groq) → CHAMP scores + holistic score + handoff kararı
  - Composite scoring (fit + qualification + engagement + sector + negative + seasonal)
  - Dynamic thresholds (project type + budget based)
  - Gap-aware prompt routing (CHAMP gaps → chat prompt yönlendirmesi)
  - Controlled score merge (floor protection, max decrease per extraction)
  - Handoff closing message (Groq)

Handoff kararı SADECE LLM judge tarafından verilir:
  1. Judge `handoff_ready=true` derse → handoff
  2. Composite score >= dynamic threshold → handoff
  3. Max messages safety net → force handoff

Devre dışı bırakılan (sadece persistence):
  - DB (PostgreSQL) — session/lead/handoff kaydı yok
  - Redis — session cache, PubSub, dedup lock yok
  - CRM webhook — handoff paketi gönderilmiyor

Kullanım:
  1. .env.simulator dosyasını düzenle
  2. python scripts/prompt_simulator.py
  3. WhatsApp'tan belirtilen numaraya mesaj at
  4. AI otomatik yanıt verir + scoring pipeline çalışır

Prompt geliştirici şu dosyaları düzenler:
  - app/domain/conversation/templates/tr/chat_system.py
  - app/domain/conversation/templates/en/chat_system.py
  - app/domain/conversation/prompts.py
  - app/domain/conversation/few_shots/*.py
  - app/domain/conversation/templates/*/qualification_judge.py

Ctrl+C ile durdur.
"""
import asyncio
import json
import os
import sys
import time
import signal
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# ── Proje root'unu path'e ekle ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
PID_FILE = PROJECT_ROOT / ".prompt_simulator.pid"


# ── Konfigürasyon ───────────────────────────────────────────────────────────

@dataclass
class SimConfig:
    # GreenAPI
    greenapi_id_instance: str = ""
    greenapi_api_token: str = ""
    greenapi_base_url: str = "https://api.green-api.com"

    # Groq
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_max_tokens: int = 4096
    groq_temperature: float = 0.7

    # Qualification Judge
    judge_model: str = "llama-3.3-70b-versatile"
    judge_max_tokens: int = 8192

    # Message Classifier
    classify_model: str = "llama-3.3-70b-versatile"

    # Simülasyon
    language: str = "tr"
    sector: str = "construction"
    tone: str = "professional"
    company_name: str = "Test Şirketi"
    persona: str = "Kıdemli Yatırım ve Proje Danışmanı"
    industry: str = "construction"
    poll_interval: int = 5           # saniye — GreenAPI polling aralığı
    debounce_seconds: float = 4.0    # ardışık mesaj bekleme süresi
    split_message_delay: float = 2.5 # --- separator sonrası bekleme

    # Scoring settings
    champ_extract_every_n: int = 3
    smart_extraction_enabled: bool = True
    signal_trigger_min_message_length: int = 15
    max_messages_before_handoff: int = 10
    score_floor_multiplier: float = 0.6
    score_min_floor: int = 30
    score_max_decrease_per_extraction: int = 10
    engagement_decay_start_minutes: int = 15

    # Sahte lead verisi (form'dan gelmiş gibi)
    fake_lead: dict = field(default_factory=lambda: {
        "lead_id": "sim-001",
        "name": "Test Müşteri",
        "phone": "",
        "source": "whatsapp",
        "project_type": "villa",
        "budget_range": "500000-1000000",
        "location_preference": "Girne",
        "notes": "",
        "form_data": {
            "what_type_of_property_are_you_interested_in": "Villa",
            "what_is_your_budget_range": "500.000 - 1.000.000 €",
            "preferred_location": "Girne / Kyrenia",
            "purpose": "Yatırım + tatil evi",
        },
    })
    lead_index: int = 0

    # Opsiyonel: company config override'ları
    working_hours: str = ""
    pricing_hints: str = ""
    kb_content: str = ""
    webhook_data_content: str = ""
    kb_file: str = ""
    webhook_data_file: str = ""
    forbidden_topics: list = field(default_factory=list)
    faq_entries: list = field(default_factory=list)
    custom_qualifying_questions: list = field(default_factory=list)
    ideal_customer_profile: str = ""
    handoff_aggressiveness: str = "balanced"

    # Initial fit score (0 = auto-compute disabled; let judge handle it)
    initial_fit_score: int = 40

    # CRM API (opsiyonel — KB docs, webhook data, company config fetch)
    crm_base_url: str = ""
    crm_api_key: str = ""
    crm_company_id: str = ""

    # Self-consistency (borderline skorlar için)
    judge_borderline_low: int = 40
    judge_borderline_high: int = 70
    judge_self_consistency_passes: int = 3

    @classmethod
    def from_env(cls, env_path: str | None = None) -> "SimConfig":
        """Ortam değişkenlerinden veya .env.simulator dosyasından yükle."""
        env_file_path: Path | None = Path(env_path).resolve() if env_path else None
        if env_path and Path(env_path).exists():
            for line in Path(env_path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

        cfg = cls()
        cfg.greenapi_id_instance = os.environ.get("SIM_GREENAPI_ID_INSTANCE", "")
        cfg.greenapi_api_token = os.environ.get("SIM_GREENAPI_API_TOKEN", "")
        cfg.groq_api_key = os.environ.get("SIM_GROQ_API_KEY", "") or os.environ.get("GROQ_API_KEY", "")
        cfg.groq_model = os.environ.get("SIM_GROQ_MODEL", cfg.groq_model)
        cfg.language = os.environ.get("SIM_LANGUAGE", cfg.language)
        cfg.sector = os.environ.get("SIM_SECTOR", cfg.sector)
        cfg.tone = os.environ.get("SIM_TONE", cfg.tone)
        cfg.company_name = os.environ.get("SIM_COMPANY_NAME", cfg.company_name)
        cfg.persona = os.environ.get("SIM_PERSONA", cfg.persona)
        cfg.industry = os.environ.get("SIM_INDUSTRY", cfg.industry)
        cfg.working_hours = os.environ.get("SIM_WORKING_HOURS", "")
        cfg.pricing_hints = os.environ.get("SIM_PRICING_HINTS", "")
        cfg.kb_content = os.environ.get("SIM_KB_CONTENT", "")
        cfg.webhook_data_content = os.environ.get("SIM_WEBHOOK_DATA_CONTENT", "")
        cfg.kb_file = os.environ.get("SIM_KB_FILE", "")
        cfg.webhook_data_file = os.environ.get("SIM_WEBHOOK_DATA_FILE", "")
        cfg.ideal_customer_profile = os.environ.get("SIM_IDEAL_CUSTOMER_PROFILE", "")
        cfg.handoff_aggressiveness = os.environ.get("SIM_HANDOFF_AGGRESSIVENESS", "balanced")

        # CRM API (opsiyonel)
        cfg.crm_base_url = os.environ.get("SIM_CRM_BASE_URL", "")
        cfg.crm_api_key = os.environ.get("SIM_CRM_API_KEY", "")
        cfg.crm_company_id = os.environ.get("SIM_CRM_COMPANY_ID", "")

        # Self-consistency
        bl = os.environ.get("SIM_JUDGE_BORDERLINE_LOW")
        if bl:
            cfg.judge_borderline_low = int(bl)
        bh = os.environ.get("SIM_JUDGE_BORDERLINE_HIGH")
        if bh:
            cfg.judge_borderline_high = int(bh)

        # Judge model
        cfg.judge_model = os.environ.get("SIM_JUDGE_MODEL", cfg.judge_model)
        cfg.judge_max_tokens = int(os.environ.get("SIM_JUDGE_MAX_TOKENS", str(cfg.judge_max_tokens)))
        cfg.classify_model = os.environ.get("SIM_CLASSIFY_MODEL", cfg.classify_model)

        # Scoring settings
        extract_n = os.environ.get("SIM_CHAMP_EXTRACT_EVERY_N")
        if extract_n:
            cfg.champ_extract_every_n = int(extract_n)

        smart = os.environ.get("SIM_SMART_EXTRACTION_ENABLED")
        if smart is not None:
            cfg.smart_extraction_enabled = smart.lower() in ("1", "true", "yes")

        max_msgs = os.environ.get("SIM_MAX_MESSAGES_BEFORE_HANDOFF")
        if max_msgs:
            cfg.max_messages_before_handoff = int(max_msgs)

        fit = os.environ.get("SIM_INITIAL_FIT_SCORE")
        if fit:
            cfg.initial_fit_score = int(fit)

        # JSON list alanları
        forbidden_raw = os.environ.get("SIM_FORBIDDEN_TOPICS", "")
        if forbidden_raw:
            try:
                cfg.forbidden_topics = json.loads(forbidden_raw)
            except json.JSONDecodeError:
                cfg.forbidden_topics = [t.strip() for t in forbidden_raw.split(",") if t.strip()]

        faq_raw = os.environ.get("SIM_FAQ_ENTRIES", "")
        if faq_raw:
            try:
                cfg.faq_entries = json.loads(faq_raw)
            except json.JSONDecodeError:
                pass

        cq_raw = os.environ.get("SIM_CUSTOM_QUALIFYING_QUESTIONS", "")
        if cq_raw:
            try:
                cfg.custom_qualifying_questions = json.loads(cq_raw)
            except json.JSONDecodeError:
                cfg.custom_qualifying_questions = [q.strip() for q in cq_raw.split(",") if q.strip()]

        lead_index = os.environ.get("SIM_LEAD_INDEX")
        if lead_index:
            cfg.lead_index = max(0, int(lead_index))

        # Groq ayarları
        max_tokens = os.environ.get("SIM_GROQ_MAX_TOKENS")
        if max_tokens:
            cfg.groq_max_tokens = int(max_tokens)

        temperature = os.environ.get("SIM_GROQ_TEMPERATURE")
        if temperature:
            cfg.groq_temperature = float(temperature)

        split_delay = os.environ.get("SIM_SPLIT_MESSAGE_DELAY")
        if split_delay:
            cfg.split_message_delay = float(split_delay)

        poll = os.environ.get("SIM_POLL_INTERVAL")
        if poll:
            cfg.poll_interval = int(poll)

        debounce = os.environ.get("SIM_DEBOUNCE_SECONDS")
        if debounce:
            cfg.debounce_seconds = float(debounce)

        # Optional KB / webhook data files
        if env_file_path is not None:
            if cfg.kb_file:
                loaded = _load_sim_content_file(cfg.kb_file, env_file_path)
                if loaded:
                    cfg.kb_content = loaded
            if cfg.webhook_data_file:
                loaded = _load_sim_content_file(cfg.webhook_data_file, env_file_path)
                if loaded:
                    cfg.webhook_data_content = loaded

        # Fake lead override (JSON string)
        lead_file = os.environ.get("SIM_FAKE_LEAD_FILE")
        if lead_file:
            lead_file_path = Path(lead_file)
            if not lead_file_path.is_absolute() and env_file_path is not None:
                lead_file_path = (env_file_path.parent / lead_file_path).resolve()
            try:
                loaded = json.loads(lead_file_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    selected = loaded[cfg.lead_index] if loaded else {}
                    cfg.fake_lead = _normalize_sim_lead(selected)
                elif isinstance(loaded, dict):
                    cfg.fake_lead = _normalize_sim_lead(loaded)
            except (OSError, json.JSONDecodeError):
                pass

        lead_json = os.environ.get("SIM_FAKE_LEAD_JSON")
        if lead_json:
            try:
                loaded = json.loads(lead_json)
                if isinstance(loaded, dict):
                    cfg.fake_lead = _normalize_sim_lead(loaded)
            except json.JSONDecodeError:
                pass

        return cfg


_SIM_TEST_PHONE = "34610285239"
_RAW_FORM_SKIP_KEYS = {
    "id", "adId", "ad_id", "adName", "ad_name", "formId", "form_id", "userId",
    "platform", "rowIndex", "syncedAt", "createdAt", "createdTime", "created_time",
    "updatedAt", "sheetName", "spreadsheetId", "campaignId", "campaign_id",
    "campaignName", "campaign_name", "organizationId", "externalLeadId", "isOrganic",
    "is_organic", "leadStatus", "adsetId", "adset_id", "adsetName", "adset_name",
    "country", "formName", "form_name", "phone_number", "full_name", "email",
}


def _build_raw_payload_from_simple_lead(lead: dict[str, Any]) -> dict[str, Any]:
    form_data = lead.get("form_data", {}) if isinstance(lead.get("form_data"), dict) else {}
    data = {
        "id": lead.get("lead_id", ""),
        "full_name": lead.get("name", ""),
        "phone_number": _SIM_TEST_PHONE,
        "email": lead.get("email", ""),
        "platform": lead.get("source", ""),
        **form_data,
    }
    return {"data": {k: v for k, v in data.items() if v not in (None, "")}}


def _load_sim_content_file(file_value: str, env_file_path: Path) -> str:
    file_path = Path(file_value)
    if not file_path.is_absolute():
        file_path = (env_file_path.parent / file_path).resolve()

    try:
        raw = file_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

    if not raw:
        return ""

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _extract_form_data(raw_payload: dict[str, Any]) -> dict[str, Any]:
    raw_data = raw_payload.get("data") if isinstance(raw_payload.get("data"), dict) else raw_payload
    if not isinstance(raw_data, dict):
        return {}
    return {
        key: value
        for key, value in raw_data.items()
        if value not in (None, "") and key not in _RAW_FORM_SKIP_KEYS
    }


def _normalize_sim_lead(record: dict[str, Any]) -> dict[str, Any]:
    from app.infrastructure.llm.field_mapper import heuristic_map

    raw_payload = record.get("raw_payload")
    if not isinstance(raw_payload, dict):
        raw_payload = _build_raw_payload_from_simple_lead(record)

    raw_payload = json.loads(json.dumps(raw_payload, ensure_ascii=False))
    raw_data = raw_payload.get("data")
    if isinstance(raw_data, dict):
        for key in ("phone_number", "phone", "mobile", "whatsapp"):
            if key in raw_data:
                raw_data[key] = _SIM_TEST_PHONE

    mapped = heuristic_map(raw_payload)
    form_data = _extract_form_data(raw_payload)

    return {
        "lead_id": mapped.external_id or record.get("lead_id") or form_data.get("id") or "sim-001",
        "name": mapped.full_name or record.get("name") or form_data.get("full_name") or "Test Müşteri",
        "phone": _SIM_TEST_PHONE,
        "email": mapped.email or record.get("email", ""),
        "city": mapped.city or record.get("city", ""),
        "source": mapped.source or record.get("source", "whatsapp"),
        "project_type": mapped.project_type or record.get("project_type", ""),
        "budget_range": mapped.budget_range or record.get("budget_range", ""),
        "notes": mapped.notes or record.get("notes", ""),
        "form_data": form_data,
        "raw_payload": raw_payload,
    }


# ── In-Memory Session ───────────────────────────────────────────────────────

@dataclass
class SimSession:
    phone: str
    lead_json: dict[str, Any]
    messages: list[dict[str, Any]] = field(default_factory=list)  # {role, content, ts}
    msg_count: int = 0
    champ_json: dict | None = None
    score: int = 0
    initial_fit_score: int = 0
    stage: str = "QUALIFYING"  # QUALIFYING | HANDOFF
    created_at: float = field(default_factory=lambda: time.time())
    _extraction_lock: bool = False
    last_processed_user_text: str = ""
    last_processed_user_ts: float = 0.0

    def add_message(self, role: str, content: str) -> dict:
        msg = {"role": role, "content": content, "ts": time.time()}
        self.messages.append(msg)
        if role == "user":
            self.msg_count += 1
        return msg


class SessionStore:
    """In-memory session store — Redis yerine."""

    def __init__(self) -> None:
        self._sessions: dict[str, SimSession] = {}  # phone -> session

    def get_or_create(self, phone: str, lead_json: dict, initial_fit: int = 40) -> SimSession:
        if phone not in self._sessions:
            lead = {**lead_json, "phone": phone}
            session = SimSession(
                phone=phone,
                lead_json=lead,
                score=initial_fit,
                initial_fit_score=initial_fit,
            )
            # Store initial_fit_score in lead_json for composite scorer
            session.lead_json["initial_fit_score"] = initial_fit
            self._sessions[phone] = session
            print(f"  [SESSION] Yeni session: {phone} (fit_score={initial_fit})")
        return self._sessions[phone]

    def get(self, phone: str) -> SimSession | None:
        return self._sessions.get(phone)

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())


# ── GreenAPI Client ──────────────────────────────────────────────────────────

class GreenAPIClient:
    def __init__(self, cfg: SimConfig) -> None:
        self.id_instance = cfg.greenapi_id_instance
        self.api_token = cfg.greenapi_api_token
        self.base_url = cfg.greenapi_base_url.rstrip("/")
        self.split_delay = cfg.split_message_delay
        self._client = httpx.AsyncClient(timeout=15)

    def _url(self, method: str) -> str:
        return f"{self.base_url}/waInstance{self.id_instance}/{method}/{self.api_token}"

    async def receive_notification(self) -> dict | None:
        try:
            resp = await self._client.get(self._url("receiveNotification"))
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as exc:
            print(f"  [WARN] GreenAPI receive hatası: {exc}")
            return None

    async def delete_notification(self, receipt_id: int) -> None:
        try:
            await self._client.delete(
                self._url("deleteNotification") + f"/{receipt_id}"
            )
        except Exception:
            pass

    async def send_message(self, chat_id: str, message: str) -> bool:
        try:
            resp = await self._client.post(
                self._url("sendMessage"),
                json={"chatId": chat_id, "message": message},
            )
            if resp.status_code == 200:
                print(f"  [SENT] → {chat_id}: {message[:80]}...")
                return True
            print(f"  [WARN] GreenAPI send {resp.status_code}: {resp.text[:100]}")
            return False
        except Exception as exc:
            print(f"  [ERROR] GreenAPI send: {exc}")
            return False

    async def send_typing(self, chat_id: str, typing_time: int = 15000) -> None:
        try:
            resp = await self._client.post(
                self._url("sendTyping"),
                json={"chatId": chat_id, "typingTime": typing_time},
            )
            if resp.status_code == 200:
                print(f"  [TYPING] → {chat_id}")
        except Exception:
            pass

    async def send_split_message(
        self, chat_id: str, ai_response: str, groq_elapsed: float = 0.0,
    ) -> None:
        from app.infrastructure.greenapi.client import compute_typing_delay

        parts = ai_response.split("---", 1)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) == 2:
            delay_p1 = compute_typing_delay(parts[0])
            remaining = max(0, delay_p1 - groq_elapsed)
            if remaining > 0:
                await asyncio.sleep(remaining)
            await self.send_message(chat_id, parts[0])

            delay_p2 = compute_typing_delay(parts[1])
            await self.send_typing(chat_id, typing_time=int(min(delay_p2 * 1000, 20000)))
            await asyncio.sleep(delay_p2)
            await self.send_message(chat_id, parts[1])
        else:
            text = ai_response.replace("---", "").strip()
            delay = compute_typing_delay(text)
            remaining = max(0, delay - groq_elapsed)
            if remaining > 0:
                await asyncio.sleep(remaining)
            await self.send_message(chat_id, text)

    async def close(self) -> None:
        await self._client.aclose()


# ── Groq Chat Client ────────────────────────────────────────────────────────

class GroqClient:
    def __init__(self, cfg: SimConfig) -> None:
        self.api_key = cfg.groq_api_key
        self.model = cfg.groq_model
        self.max_tokens = cfg.groq_max_tokens
        self.temperature = cfg.groq_temperature
        self.judge_model = cfg.judge_model
        self.judge_max_tokens = cfg.judge_max_tokens
        self.classify_model = cfg.classify_model
        self._client = httpx.AsyncClient(
            timeout=30,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def chat(self, messages: list[dict], model: str | None = None,
                   max_tokens: int | None = None, temperature: float | None = None) -> str:
        """Non-streaming chat completion."""
        resp = await self._client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": model or self.model,
                "messages": messages,
                "max_tokens": max_tokens or self.max_tokens,
                "temperature": temperature if temperature is not None else self.temperature,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def chat_json(self, messages: list[dict], model: str | None = None,
                        max_tokens: int | None = None) -> str:
        """Non-streaming chat with JSON response format."""
        resp = await self._client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": model or self.judge_model,
                "messages": messages,
                "max_tokens": max_tokens or self.judge_max_tokens,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def close(self) -> None:
        await self._client.aclose()


# ── Prompt Builder (gerçek projedeki prompt'ları kullanır) ────────────────────

class CRMClient:
    """Opsiyonel CRM REST API client — KB docs, webhook data, company config fetch."""

    def __init__(self, cfg: SimConfig) -> None:
        self.cfg = cfg
        self._client: httpx.AsyncClient | None = None
        self._crm_config_cache: dict | None = None
        self._kb_cache: str = ""
        self._webhook_data_cache: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.crm_base_url and self.cfg.crm_api_key)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.cfg.crm_base_url.rstrip("/"),
                headers={"X-API-KEY": self.cfg.crm_api_key},
                timeout=httpx.Timeout(10.0),
            )
        return self._client

    async def fetch_company_ai_config(self) -> dict | None:
        """CRM'den company AI config çek (production ile aynı)."""
        if not self.enabled or not self.cfg.crm_company_id:
            return None
        if self._crm_config_cache is not None:
            return self._crm_config_cache
        try:
            client = self._get_client()
            resp = await client.get(
                f"/internal/company/{self.cfg.crm_company_id}/ai-config",
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            self._crm_config_cache = resp.json()
            print(f"  [CRM] Company AI config yüklendi")
            return self._crm_config_cache
        except Exception as exc:
            print(f"  [WARN] CRM AI config fetch hatası: {str(exc)[:80]}")
            return None

    async def fetch_kb_content(self) -> str:
        """CRM'den KB dokümanlarını çek ve birleştir (production ile aynı)."""
        if not self.enabled or not self.cfg.crm_company_id:
            return ""
        if self._kb_cache:
            return self._kb_cache
        try:
            client = self._get_client()
            resp = await client.get(
                f"/internal/company/{self.cfg.crm_company_id}/kb-documents",
                params={"include_content": "true"},
            )
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            docs = resp.json()
            if not isinstance(docs, list):
                return ""
            combined = "\n\n---\n\n".join(
                f"[{doc.get('file_name', 'unknown')}]\n{doc.get('content', '')}"
                for doc in docs
                if doc.get("active", True) and doc.get("content")
            )
            self._kb_cache = combined[:8000] if combined else ""
            if self._kb_cache:
                print(f"  [CRM] KB dokümanları yüklendi ({len(docs)} dosya, {len(self._kb_cache)} char)")
            return self._kb_cache
        except Exception as exc:
            print(f"  [WARN] CRM KB docs fetch hatası: {str(exc)[:80]}")
            return ""

    async def fetch_webhook_data_content(self) -> str:
        """CRM'den webhook verilerini çek (production ile aynı — RAG enjeksiyonu)."""
        if not self.enabled or not self.cfg.crm_company_id:
            return ""
        if self._webhook_data_cache:
            return self._webhook_data_cache
        try:
            client = self._get_client()
            resp = await client.get(
                f"/internal/company/{self.cfg.crm_company_id}/webhook-data",
                params={"include_content": "true"},
            )
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            entries = resp.json()
            if not isinstance(entries, list):
                return ""
            webhook_combined = "\n\n---\n\n".join(
                f"[webhook:{entry.get('id', 'unknown')}]\n"
                + json.dumps(entry.get("payload", {}), ensure_ascii=False, indent=2)
                for entry in entries
            )
            self._webhook_data_cache = webhook_combined[:8000] if webhook_combined else ""
            if self._webhook_data_cache:
                print(f"  [CRM] Webhook data yüklendi ({len(entries)} entry, {len(self._webhook_data_cache)} char)")
            return self._webhook_data_cache
        except Exception as exc:
            print(f"  [WARN] CRM webhook data fetch hatası: {str(exc)[:80]}")
            return ""

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class PromptBuilder:
    def __init__(self, cfg: SimConfig, crm: CRMClient) -> None:
        self.cfg = cfg
        self.crm = crm
        self._dynamic_config: dict | None = None

    async def init(self) -> None:
        """Başlangıçta CRM'den config + KB + webhook data yükle."""
        if self.crm.enabled:
            # CRM'den company AI config çek (simulator config ile merge)
            crm_config = await self.crm.fetch_company_ai_config()
            if crm_config:
                self._dynamic_config = crm_config

            # KB docs ve webhook data çek
            await self.crm.fetch_kb_content()
            await self.crm.fetch_webhook_data_content()

    def _company_config(self) -> dict:
        # Start with simulator static config
        kb = self.cfg.kb_content
        if self.cfg.webhook_data_content:
            kb = (kb + "\n\n---\n\n" + self.cfg.webhook_data_content) if kb else self.cfg.webhook_data_content

        # Merge CRM KB + webhook data (production behavior)
        crm_kb = self.crm._kb_cache if self.crm.enabled else ""
        crm_webhook = self.crm._webhook_data_cache if self.crm.enabled else ""
        if crm_kb:
            kb = (kb + "\n\n---\n\n" + crm_kb) if kb else crm_kb
        if crm_webhook:
            kb = (kb + "\n\n---\n\n" + crm_webhook) if kb else crm_webhook

        # Truncate total KB at 8000 chars (production behavior)
        if kb and len(kb) > 8000:
            kb = kb[:8000]

        base = {
            "primary_language": self.cfg.language,
            "industry_focus": self.cfg.sector,
            "tone": self.cfg.tone,
            "company_display_name": self.cfg.company_name,
            "custom_persona": self.cfg.persona,
            "working_hours": self.cfg.working_hours,
            "pricing_hints": self.cfg.pricing_hints,
            "kb_documents_content": kb,
            "forbidden_topics": self.cfg.forbidden_topics,
            "faq_entries": self.cfg.faq_entries,
            "custom_qualifying_questions": self.cfg.custom_qualifying_questions,
            "ideal_customer_profile": self.cfg.ideal_customer_profile,
            "handoff_aggressiveness": self.cfg.handoff_aggressiveness,
        }

        # Merge CRM config (CRM overrides simulator defaults where present)
        if self._dynamic_config:
            for key in ("primary_language", "industry_focus", "tone",
                        "company_display_name", "custom_persona", "working_hours",
                        "pricing_hints", "forbidden_topics", "faq_entries",
                        "custom_qualifying_questions", "ideal_customer_profile",
                        "handoff_aggressiveness", "scoring_weights",
                        "max_messages_before_handoff", "qualification_threshold",
                        "scoring_mode"):
                if key in self._dynamic_config and self._dynamic_config[key]:
                    base[key] = self._dynamic_config[key]

        return base

    def build_system_prompt(self, lead_json: dict, champ_json: dict | None = None) -> str:
        from app.domain.conversation.prompts import build_chat_system_prompt
        return build_chat_system_prompt(lead_json, champ_json, self._company_config())

    def build_judge_prompt(self, conversation_text: str, lead_json: dict,
                           current_judgment_json: dict | None = None) -> str:
        from app.domain.conversation.prompts import build_qualification_judge_prompt
        return build_qualification_judge_prompt(
            conversation_history=conversation_text,
            lead_json=lead_json,
            current_judgment_json=current_judgment_json,
            company_config=self._company_config(),
        )

    def build_closing_prompt(self) -> str:
        from app.domain.conversation.prompts import build_handoff_closing_prompt
        return build_handoff_closing_prompt(company_config=self._company_config())


# ── Scoring Engine (production pipeline — no I/O) ───────────────────────────

class ScoringEngine:
    """
    Production scoring pipeline simülasyonu.
    Tüm domain logic'i kullanır, sadece persistence katmanı yok.
    """

    def __init__(self, cfg: SimConfig, groq: GroqClient, prompts: PromptBuilder) -> None:
        self.cfg = cfg
        self.groq = groq
        self.prompts = prompts

    # ── Conversation end detection (extraction tetikleyici, handoff değil) ────

    def check_conversation_end(self, message: str) -> tuple[bool, str]:
        from app.domain.scoring.signals.handoff_triggers import check_conversation_end
        return check_conversation_end(message, self.cfg.language)

    # ── Message analysis (rule-based + LLM) ──────────────────────────────────

    async def analyze_message(
        self, message: str, messages: list[dict], msg_count: int,
    ) -> tuple[Any, bool, str]:
        """
        Returns (signal_result, should_extract, trigger_reason).
        Mirrors production ConversationHandler.handle_message layers.
        """
        from app.domain.scoring.signals.message_analyzer import MessageAnalyzer

        # LLM classification (Groq) — same as production tier 1
        llm_result = await self._classify_message(message, messages, msg_count)

        analyzer = MessageAnalyzer()
        signal_result = analyzer.analyze(
            message=message,
            messages=messages,
            msg_count=msg_count,
            language=self.cfg.language,
            sector=self.cfg.sector,
            extract_every_n=self.cfg.champ_extract_every_n,
            min_message_length=self.cfg.signal_trigger_min_message_length,
            llm_classification=llm_result,
        )

        # Smart extraction decision
        if self.cfg.smart_extraction_enabled:
            should_extract = signal_result.should_trigger_extraction
        else:
            should_extract = (msg_count % self.cfg.champ_extract_every_n == 0)

        return signal_result, should_extract, signal_result.trigger_reason

    async def _classify_message(self, message: str, messages: list[dict],
                                 msg_count: int) -> Any:
        """LLM message classification via Groq (production tier 1)."""
        from app.infrastructure.llm.message_classifier import (
            _CLASSIFICATION_PROMPT, _build_context, _parse_response,
        )

        stage = "early" if msg_count <= 3 else ("mid" if msg_count <= 6 else "late")
        prompt = _CLASSIFICATION_PROMPT.format(
            stage=stage,
            context=_build_context(messages),
            message=message[:500],
        )

        try:
            raw = await self.groq.chat_json(
                messages=[
                    {"role": "system", "content": (
                        "You classify messages for a Turkish/English construction lead chat. "
                        "Return ONLY a single-line JSON object. All values in English. "
                        "No Turkish characters in output. No markdown."
                    )},
                    {"role": "user", "content": prompt},
                ],
                model=self.groq.classify_model,
                max_tokens=200,
            )
            result = _parse_response(raw)
            result.source = "groq"
            return result
        except Exception as exc:
            print(f"  [WARN] LLM classify hatası: {str(exc)[:80]}")
            return None

    # ── Qualification Judge extraction (Groq) ─────────────────────────────────

    async def run_qualification_judge(self, session: SimSession) -> tuple[dict, Any] | None:
        """
        Production qualification judge — Groq üzerinden.
        Includes self-consistency for borderline scores (production behavior).
        Returns (champ_json_dict, merged_champ_score) or None on failure.
        """
        from app.infrastructure.llm.schemas import QualificationJudgmentResult
        from app.domain.scoring.qualification_judgment import QualificationJudgment

        messages_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in session.messages
        )
        if not messages_text.strip():
            return None

        judge_prompt = self.prompts.build_judge_prompt(
            conversation_text=messages_text,
            lead_json=session.lead_json,
            current_judgment_json=session.champ_json,
        )

        try:
            raw = await self.groq.chat_json(
                messages=[{"role": "system", "content": judge_prompt}],
                model=self.groq.judge_model,
                max_tokens=self.groq.judge_max_tokens,
            )
            result = self._parse_judgment(raw)
        except Exception as exc:
            print(f"  [WARN] Judge extraction hatası: {str(exc)[:100]}")
            # Fallback: retry with chat model
            try:
                raw = await self.groq.chat_json(
                    messages=[{"role": "system", "content": judge_prompt}],
                    model=self.groq.model,
                    max_tokens=self.groq.judge_max_tokens,
                )
                result = self._parse_judgment(raw)
            except Exception as exc2:
                print(f"  [ERROR] Judge fallback hatası: {str(exc2)[:100]}")
                return None

        extraction_version = (
            QualificationJudgment.from_dict(session.champ_json).extraction_version + 1
            if session.champ_json
            else 1
        )
        judgment = QualificationJudgment.from_judgment_result(result, extraction_version)

        # Monotonic merge with existing
        if session.champ_json:
            old = QualificationJudgment.from_dict(session.champ_json)
            judgment = old.merge_monotonic(judgment)

        # ── Self-consistency for borderline scores (production behavior) ──
        if self.cfg.judge_borderline_low <= judgment.holistic_score <= self.cfg.judge_borderline_high:
            print(f"  [SELF-CONSISTENCY] Borderline holistic={judgment.holistic_score}, "
                  f"{self.cfg.judge_self_consistency_passes} pass çalıştırılıyor...")
            try:
                sc_result = await self._run_self_consistency(judge_prompt)
                sc_judgment = QualificationJudgment.from_judgment_result(
                    sc_result, judgment.extraction_version,
                )
                judgment = judgment.merge_monotonic(sc_judgment)
                print(f"  [SELF-CONSISTENCY] Merged holistic={judgment.holistic_score}")
            except Exception as exc:
                print(f"  [WARN] Self-consistency hatası: {str(exc)[:80]}")

        return judgment.to_champ_dict(), judgment.to_champ_score()

    async def _run_self_consistency(self, judge_prompt: str):
        """Run multiple judge passes and average scores (production behavior)."""
        import asyncio as _asyncio
        from app.infrastructure.llm.schemas import QualificationJudgmentResult

        async def _single_pass(temp: float) -> QualificationJudgmentResult:
            raw = await self.groq.chat_json(
                messages=[{"role": "system", "content": judge_prompt}],
                model=self.groq.judge_model,
                max_tokens=self.groq.judge_max_tokens,
            )
            return self._parse_judgment(raw)

        temps = [0.0] + [0.3] * (self.cfg.judge_self_consistency_passes - 1)
        tasks = [_single_pass(t) for t in temps[:self.cfg.judge_self_consistency_passes]]
        results = await _asyncio.gather(*tasks, return_exceptions=True)

        valid = [r for r in results if isinstance(r, QualificationJudgmentResult)]
        if not valid:
            raise ValueError("All self-consistency passes failed")
        if len(valid) == 1:
            return valid[0]

        def _avg(values: list[int]) -> int:
            return round(sum(values) / len(values))

        all_neg: set[str] = set()
        for r in valid:
            all_neg.update(r.negative_signals)
        best = max(valid, key=lambda r: len(r.thinking))

        return QualificationJudgmentResult(
            thinking=best.thinking,
            challenges_score=_avg([r.challenges_score for r in valid]),
            challenges_reasoning=best.challenges_reasoning,
            challenges_confidence=min(r.challenges_confidence for r in valid),
            authority_score=_avg([r.authority_score for r in valid]),
            authority_reasoning=best.authority_reasoning,
            authority_confidence=min(r.authority_confidence for r in valid),
            money_score=_avg([r.money_score for r in valid]),
            money_reasoning=best.money_reasoning,
            money_confidence=min(r.money_confidence for r in valid),
            prioritization_score=_avg([r.prioritization_score for r in valid]),
            prioritization_reasoning=best.prioritization_reasoning,
            prioritization_confidence=min(r.prioritization_confidence for r in valid),
            holistic_score=_avg([r.holistic_score for r in valid]),
            holistic_reasoning=best.holistic_reasoning,
            icp_fit_assessment=best.icp_fit_assessment,
            negative_signals=list(all_neg),
            negative_penalty=min(r.negative_penalty for r in valid),
            negative_reasoning=best.negative_reasoning,
            sector_qualifiers=best.sector_qualifiers,
            missing_info=best.missing_info,
            recommended_next_question=best.recommended_next_question,
            confidence=best.confidence,
        )

    def _parse_judgment(self, text: str):
        """Parse judge response into QualificationJudgmentResult."""
        import re
        from app.infrastructure.llm.schemas import QualificationJudgmentResult

        try:
            data = json.loads(text)
            return QualificationJudgmentResult.model_validate(data)
        except (json.JSONDecodeError, Exception):
            pass

        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                return QualificationJudgmentResult.model_validate(data)
            except (json.JSONDecodeError, Exception):
                pass

        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            data = json.loads(text[brace_start : brace_end + 1])
            return QualificationJudgmentResult.model_validate(data)

        raise ValueError(f"Judge parse failed: {text[:200]}")

    # ── Composite scoring (production pipeline) ───────────────────────────────

    def compute_composite_score(
        self, session: SimSession, champ_json: dict, merged_champ,
    ) -> int:
        """
        Full composite scoring — same as production extract_champ_task.
        Returns the new score after controlled merge.
        """
        from app.domain.scoring.composite_scorer import CompositeScorer, ScoringWeights
        from app.domain.scoring.engagement import compute_engagement_score
        from app.domain.scoring.thresholds import compute_threshold
        from app.domain.scoring.signals.registry import SignalRegistry

        # Engagement score
        eng_result = compute_engagement_score(
            session.messages,
            current_time=time.time(),
            decay_start_minutes=self.cfg.engagement_decay_start_minutes,
        )

        # Negative signals (from judge output)
        neg_adjustment = champ_json.get("negative_penalty", 0)

        # Sector qualifier bonus
        sector_qualifier = SignalRegistry.get_sector_qualifier(self.cfg.sector)
        sector_bonus = sector_qualifier.compute_bonus(champ_json)

        # Seasonal modifier
        seasonal = SignalRegistry.get_seasonal_modifier(self.cfg.sector)

        # Company config for weights
        company_config = self.prompts._company_config()
        weights = ScoringWeights.from_config(company_config)
        scorer = CompositeScorer(weights)

        # Judge mode: use holistic_score directly (same as production)
        if champ_json.get("holistic_score"):
            holistic = champ_json["holistic_score"]
            eng_boost = int(eng_result.score * 0.10)
            raw_score = holistic + eng_boost + int(neg_adjustment * 0.5) + seasonal
            final_score = max(0, min(100, raw_score))

            from app.domain.scoring.composite_scorer import CompositeResult
            result = CompositeResult(
                final_score=final_score,
                fit_score=session.initial_fit_score,
                qualification_score=merged_champ.total if merged_champ else 0,
                engagement_score=eng_result.score,
                negative_adjustment=neg_adjustment,
                sector_bonus=sector_bonus,
                seasonal_modifier=seasonal,
                confidence_multiplier=1.0,
                raw_weighted=float(holistic),
            )
        else:
            result = scorer.compute(
                fit_score=session.initial_fit_score,
                champ=merged_champ,
                engagement_score=eng_result.score,
                negative_adjustment=neg_adjustment,
                sector_bonus=sector_bonus,
                seasonal_modifier=seasonal,
            )

        # Controlled merge with floor protection
        score_floor = max(
            int(session.initial_fit_score * self.cfg.score_floor_multiplier),
            self.cfg.score_min_floor,
        )
        new_score = CompositeScorer.controlled_merge(
            session.score,
            result,
            score_floor=score_floor,
            max_decrease=self.cfg.score_max_decrease_per_extraction,
        )

        # Print detailed scoring breakdown
        print(f"  [SCORE] Composite breakdown:")
        print(f"    fit={session.initial_fit_score}, qualification={result.qualification_score}, "
              f"engagement={eng_result.score}, sector_bonus={sector_bonus}")
        print(f"    negative={neg_adjustment}, seasonal={seasonal}")
        if champ_json.get("holistic_score"):
            print(f"    holistic={champ_json['holistic_score']}, eng_boost={int(eng_result.score*0.10)}")
        print(f"    raw_weighted={result.raw_weighted:.1f}, confidence_mult={result.confidence_multiplier:.3f}")
        print(f"    final={result.final_score} → controlled_merge → {new_score} (floor={score_floor})")

        if eng_result.details:
            print(f"    engagement: {', '.join(eng_result.details)}")

        return new_score

    # ── Threshold check ─────────────────────────────────────────────────────

    def compute_threshold(self, lead_json: dict) -> int:
        from app.domain.scoring.thresholds import compute_threshold
        extra = lead_json.get("extra_data", {})
        return compute_threshold(
            project_type=lead_json.get("project_type") or extra.get("project_type", ""),
            budget_range=lead_json.get("budget_range") or extra.get("budget_range", ""),
            company_config=self.prompts._company_config(),
        )

    # ── Full extraction + scoring cycle ──────────────────────────────────────

    # ── Lead enrichment (production behavior) ───────────────────────────────

    def enrich_lead(self, session: SimSession, champ_json: dict) -> None:
        """
        Judge'dan çıkan extracted fields'ları lead_json'a yaz.
        Production'daki enrich_lead_from_extraction() ile aynı.
        """
        from app.domain.qualification.lead_enrichment import enrich_lead_from_extraction

        enriched = enrich_lead_from_extraction(
            session.lead_json, champ_json, session.messages,
        )
        enriched_fields = enriched.get("enriched_fields", [])
        if enriched_fields:
            session.lead_json = enriched
            print(f"  [ENRICH] Lead zenginleştirildi: {', '.join(enriched_fields)}")

    # ── Reasoning report (Groq — production uses local LLM) ──────────────

    async def generate_reasoning_report(self, session: SimSession) -> dict | None:
        """
        Production'da local LLM ile yapılan reasoning report'u Groq ile üretir.
        """
        from app.domain.conversation.prompts import build_reasoning_report_prompt

        breakdown = {"total": session.score, "note": "from_chat_path"}
        if session.champ_json:
            breakdown = {
                "challenges": session.champ_json.get("challenges_score", 0),
                "authority": session.champ_json.get("authority_score", 0),
                "money": session.champ_json.get("money_score", 0),
                "prioritization": session.champ_json.get("prioritization_score", 0),
                "total": session.score,
            }

        try:
            prompt = build_reasoning_report_prompt(
                session.lead_json, session.score, breakdown,
                session.champ_json,
                company_config=self.prompts._company_config(),
            )
            raw = await self.groq.chat_json(
                messages=[{"role": "user", "content": prompt}],
                model=self.groq.judge_model,
                max_tokens=2048,
            )
            report = json.loads(raw)

            # Enrich with judge analysis (production behavior)
            if session.champ_json and session.champ_json.get("holistic_reasoning"):
                report["qualification_analysis"] = {
                    "holistic_score": session.champ_json.get("holistic_score"),
                    "holistic_reasoning": session.champ_json.get("holistic_reasoning"),
                    "icp_fit_assessment": session.champ_json.get("icp_fit_assessment"),
                    "negative_signals": session.champ_json.get("negative_signals", []),
                    "scoring_mode": session.champ_json.get("scoring_mode"),
                }

            print(f"  [REASONING] Rapor üretildi: {report.get('summary', '')[:80]}...")
            return report
        except Exception as exc:
            print(f"  [WARN] Reasoning report hatası: {str(exc)[:80]}")
            return None

    # ── Signal summary + outreach mapping ─────────────────────────────────

    def build_handoff_package(self, session: SimSession, reasoning_json: dict | None) -> dict:
        """
        Production HandoffHandler ile aynı handoff paketi oluşturur.
        CRM'e gönderilmez ama loglanır.
        """
        from app.domain.qualification.outreach_mapping import (
            build_outreach_payload, build_signal_summary,
        )

        msg_dicts = session.messages
        composite_breakdown = None  # Would come from score_repo in production

        signal_summary = build_signal_summary(
            session.champ_json, composite_breakdown, msg_dicts,
        )

        outreach = build_outreach_payload(
            session_id=f"sim-{session.phone}",
            lead_json=session.lead_json,
            score=session.score,
            champ_json=session.champ_json,
            reasoning_json=reasoning_json,
            messages=msg_dicts,
            composite_breakdown=composite_breakdown,
            signal_summary=signal_summary,
            handoff_path="chat",
        )

        pre_score = session.lead_json.get("initial_fit_score", 0)
        handoff_package = {
            "raw_payload": session.lead_json.get("raw_payload", {}),
            "lead": session.lead_json,
            "pre_score": pre_score,
            "qualified_score": session.score,
            "reasoning_report": reasoning_json,
            "champ": session.champ_json,
            "session_id": f"sim-{session.phone}",
            "path": "chat",
            "conversation_transcript": [
                {"role": m["role"], "content": m["content"], "ts": m.get("ts")}
                for m in session.messages
            ],
            "signal_summary": signal_summary,
            "outreach": outreach,
        }

        return handoff_package

    # ── Full extraction + scoring cycle ──────────────────────────────────────

    async def extract_and_score(self, session: SimSession, force_handoff: bool = False) -> dict:
        """
        Full production extraction cycle:
          1. Run qualification judge (+ self-consistency for borderline)
          2. Enrich lead from extraction
          3. Compute composite score
          4. Check handoff conditions
        Returns status dict.
        """
        if session._extraction_lock:
            return {"status": "locked"}
        session._extraction_lock = True

        try:
            # Run qualification judge via Groq
            judge_result = await self.run_qualification_judge(session)
            if judge_result is None:
                return {"status": "extraction_failed"}

            champ_json, merged_champ = judge_result

            # Enrich lead from judge extraction (production behavior)
            self.enrich_lead(session, champ_json)

            # Compute composite score
            new_score = self.compute_composite_score(session, champ_json, merged_champ)

            old_score = session.score
            session.champ_json = champ_json
            session.score = new_score

            # Print CHAMP details
            print(f"  [CHAMP] C={champ_json.get('challenges_score',0)} "
                  f"A={champ_json.get('authority_score',0)} "
                  f"M={champ_json.get('money_score',0)} "
                  f"P={champ_json.get('prioritization_score',0)} "
                  f"total={champ_json.get('total',0)}")
            if champ_json.get("holistic_reasoning"):
                print(f"  [JUDGE] {champ_json['holistic_reasoning'][:120]}...")
            if champ_json.get("recommended_next_question"):
                print(f"  [GAP] Önerilen soru: {champ_json['recommended_next_question'][:100]}")

            print(f"  [SCORE] {old_score} → {new_score}")

            # ── Handoff priority chain ────────────────────────────────────────

            # Priority 1: Judge says ready
            if champ_json.get("handoff_ready"):
                reason = champ_json.get("handoff_reason", "judge_decision")
                print(f"  [HANDOFF] Judge kararı: {reason}")
                return {
                    "status": "handoff",
                    "trigger": "judge_ready",
                    "reason": reason,
                    "score": new_score,
                }

            # Priority 2: Score threshold
            threshold = self.compute_threshold(session.lead_json)
            if new_score >= threshold:
                print(f"  [HANDOFF] Threshold aşıldı: {new_score} >= {threshold}")
                return {
                    "status": "handoff",
                    "trigger": "threshold",
                    "score": new_score,
                    "threshold": threshold,
                }

            # Priority 3: Force handoff (max messages)
            if force_handoff:
                print(f"  [HANDOFF] Force handoff (max messages)")
                return {
                    "status": "handoff",
                    "trigger": "force_max_messages",
                    "score": new_score,
                }

            return {"status": "scored", "score": new_score, "threshold": threshold}

        finally:
            session._extraction_lock = False


# ── Debounce Buffer ──────────────────────────────────────────────────────────

class DebounceBuffer:
    def __init__(self, debounce_seconds: float) -> None:
        self._debounce = debounce_seconds
        self._buffers: dict[str, list[str]] = {}
        self._timestamps: dict[str, float] = {}

    def add(self, phone: str, text: str) -> None:
        self._buffers.setdefault(phone, []).append(text)
        self._timestamps[phone] = time.time()

    def is_ready(self, phone: str) -> bool:
        ts = self._timestamps.get(phone)
        if ts is None:
            return False
        return (time.time() - ts) >= self._debounce

    def flush(self, phone: str) -> str | None:
        msgs = self._buffers.pop(phone, [])
        self._timestamps.pop(phone, None)
        if not msgs:
            return None
        return "\n".join(msgs)

    def pending_phones(self) -> list[str]:
        return list(self._buffers.keys())


# ── Ana Simülasyon Loop ──────────────────────────────────────────────────────

class PromptSimulator:
    def __init__(self, cfg: SimConfig) -> None:
        self.cfg = cfg
        self.sessions = SessionStore()
        self.greenapi = GreenAPIClient(cfg)
        self.groq = GroqClient(cfg)
        self.crm = CRMClient(cfg)
        self.prompts = PromptBuilder(cfg, self.crm)
        self.scoring = ScoringEngine(cfg, self.groq, self.prompts)
        self.buffer = DebounceBuffer(cfg.debounce_seconds)
        self._seen: set[str] = set()
        self._running = True

    async def _send_greeting(self, phone: str) -> None:
        """Form gelmiş gibi ilk selamlama mesajını AI üzerinden gönder."""
        session = self.sessions.get_or_create(
            phone, self.cfg.fake_lead, self.cfg.initial_fit_score,
        )

        print(f"  [GREETING] {phone} numarasına ilk mesaj gönderiliyor...")

        system_prompt = self.prompts.build_system_prompt(
            lead_json=session.lead_json,
            champ_json=None,
        )

        messages = [{"role": "system", "content": system_prompt}]

        chat_id = f"{phone}@c.us"
        await self.greenapi.send_typing(chat_id, typing_time=20000)

        t0 = time.time()
        try:
            ai_response = await self.groq.chat(messages)
        except Exception as exc:
            print(f"  [ERROR] Groq greeting hatası: {exc}")
            return
        groq_elapsed = time.time() - t0

        session.add_message("assistant", ai_response)
        print(f"  [AI] → {phone}: {ai_response[:120]}...")

        await self.greenapi.send_split_message(chat_id, ai_response, groq_elapsed=groq_elapsed)
        print(f"  [GREETING] İlk mesaj gönderildi. Yanıt bekleniyor...\n")

    async def _handle_handoff(self, session: SimSession, trigger: str, reason: str = "") -> None:
        """
        Full production handoff flow:
          1. Reasoning report generation (Groq)
          2. Closing message (Groq)
          3. Signal summary + outreach mapping
          4. Full handoff package logging
        """
        session.stage = "HANDOFF"
        chat_id = f"{session.phone}@c.us"

        print(f"\n{'='*60}")
        print(f"  HANDOFF — {session.phone}")
        print(f"  Trigger: {trigger}")
        if reason:
            print(f"  Reason: {reason}")
        print(f"  Final Score: {session.score}")
        if session.champ_json:
            print(f"  CHAMP: C={session.champ_json.get('challenges_score',0)} "
                  f"A={session.champ_json.get('authority_score',0)} "
                  f"M={session.champ_json.get('money_score',0)} "
                  f"P={session.champ_json.get('prioritization_score',0)}")
            if session.champ_json.get("holistic_score"):
                print(f"  Holistic: {session.champ_json['holistic_score']}")
            if session.champ_json.get("icp_fit_assessment"):
                print(f"  ICP Fit: {session.champ_json['icp_fit_assessment'][:100]}")
        print(f"  Messages: {session.msg_count}")

        # Enriched fields
        enriched = session.lead_json.get("enriched_fields", [])
        if enriched:
            print(f"  Enriched: {', '.join(enriched)}")

        print(f"{'='*60}")

        # ── 1. Reasoning report (Groq — production uses local LLM) ────────
        reasoning_json = await self.scoring.generate_reasoning_report(session)

        # ── 2. Closing message via Groq ───────────────────────────────────
        try:
            closing_prompt = self.prompts.build_closing_prompt()
            closing_msg = await self.groq.chat(
                [{"role": "system", "content": closing_prompt}],
                temperature=0.5,
            )
            await self.greenapi.send_typing(chat_id, typing_time=10000)
            await asyncio.sleep(2)
            await self.greenapi.send_message(chat_id, closing_msg.replace("---", "").strip())
            print(f"  [CLOSING] → {session.phone}: {closing_msg[:100]}...")
        except Exception as exc:
            print(f"  [WARN] Closing message hatası: {exc}")

        # ── 3. Build full handoff package (production-equivalent) ─────────
        handoff_package = self.scoring.build_handoff_package(session, reasoning_json)

        # Print signal summary
        signal_summary = handoff_package.get("signal_summary", {})
        if signal_summary:
            rec = signal_summary.get("recommendation", "")
            if rec:
                print(f"  [SIGNAL SUMMARY] {rec}")
            buying = signal_summary.get("buying_signals", [])
            if buying:
                print(f"  [BUYING SIGNALS] {', '.join(buying)}")
            negatives = signal_summary.get("negative_signals", [])
            if negatives:
                print(f"  [NEGATIVE SIGNALS] {', '.join(negatives)}")
            key_facts = signal_summary.get("key_facts", [])
            for fact in key_facts[:3]:
                print(f"  [KEY FACT] {fact[:100]}")
            missing = signal_summary.get("missing_info_for_sales", [])
            if missing:
                print(f"  [MISSING FOR SALES] {', '.join(missing[:3])}")

        print(f"\n  [PACKAGE] Handoff paketi hazır ({len(json.dumps(handoff_package))} bytes)")
        print(f"  [PACKAGE] Production'da CRM webhook'a gönderilir")
        print(f"{'='*60}\n")

    async def run(self) -> None:
        target_phone = self.cfg.fake_lead.get("phone", "")

        print("=" * 60)
        print("  PROMPT SIMULATOR (FULL SCORING PIPELINE)")
        print("=" * 60)
        print(f"  GreenAPI Instance : {self.cfg.greenapi_id_instance}")
        print(f"  Groq Chat Model   : {self.cfg.groq_model}")
        print(f"  Judge Model       : {self.cfg.judge_model}")
        print(f"  Classify Model    : {self.cfg.classify_model}")
        print(f"  Dil / Sektör      : {self.cfg.language} / {self.cfg.sector}")
        print(f"  Şirket            : {self.cfg.company_name}")
        print(f"  Ton               : {self.cfg.tone}")
        print(f"  Hedef Telefon     : {target_phone or '(yok — pasif mod)'}")
        print(f"  Initial Fit Score : {self.cfg.initial_fit_score}")
        print(f"  Aggressiveness    : {self.cfg.handoff_aggressiveness}")
        print(f"  Smart Extraction  : {'ON' if self.cfg.smart_extraction_enabled else 'OFF'}")
        print(f"  Self-Consistency  : borderline [{self.cfg.judge_borderline_low}-{self.cfg.judge_borderline_high}]")
        print(f"  Extract Every N   : {self.cfg.champ_extract_every_n}")
        print(f"  Max Messages      : {self.cfg.max_messages_before_handoff}")
        print(f"  CRM API           : {'ON → ' + self.cfg.crm_base_url if self.crm.enabled else 'OFF (statik config)'}")
        print(f"  KB Data           : {'ON' if bool(self.cfg.kb_content.strip()) else 'OFF'}")
        print(f"  RAG Data          : {'ON' if bool(self.cfg.webhook_data_content.strip()) else 'OFF'}")
        print(f"  Poll Aralığı      : {self.cfg.poll_interval}s")
        print(f"  Debounce          : {self.cfg.debounce_seconds}s")
        print("=" * 60)

        # CRM'den KB docs, webhook data, company config yükle (varsa)
        await self.prompts.init()

        if target_phone:
            print(f"  Form simülasyonu başlatılıyor → {target_phone}\n")
            await self._send_greeting(target_phone)
        else:
            print("  WhatsApp'tan mesaj bekleniyor... (Ctrl+C ile durdur)\n")

        try:
            while self._running:
                await self._poll_notifications()
                await self._process_ready_buffers()
                await asyncio.sleep(self.cfg.poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            await self.greenapi.close()
            await self.groq.close()
            await self.crm.close()
            print("\n  Simulator durduruldu.")

    def stop(self) -> None:
        self._running = False

    async def _poll_notifications(self) -> None:
        while True:
            notification = await self.greenapi.receive_notification()
            if notification is None:
                break

            receipt_id = notification.get("receiptId")
            body = notification.get("body", {})
            type_webhook = body.get("typeWebhook", "")

            if type_webhook != "incomingMessageReceived":
                if receipt_id:
                    await self.greenapi.delete_notification(receipt_id)
                continue

            msg_data = body.get("messageData", {})
            type_message = msg_data.get("typeMessage", "")
            if type_message not in ("textMessage", "extendedTextMessage"):
                if receipt_id:
                    await self.greenapi.delete_notification(receipt_id)
                continue

            id_message = body.get("idMessage", "")
            sender = body.get("senderData", {})
            chat_id = sender.get("chatId", "")
            phone = sender.get("sender", "").replace("@c.us", "")
            sender_name = sender.get("senderName", phone)

            if type_message == "textMessage":
                text = msg_data.get("textMessageData", {}).get("textMessage", "")
            else:
                text = msg_data.get("extendedTextMessageData", {}).get("text", "")

            if id_message in self._seen:
                if receipt_id:
                    await self.greenapi.delete_notification(receipt_id)
                continue
            self._seen.add(id_message)

            if not chat_id.endswith("@c.us"):
                if receipt_id:
                    await self.greenapi.delete_notification(receipt_id)
                continue

            print(f"  [RECV] ← {sender_name} ({phone}): {text}")

            self.sessions.get_or_create(
                phone, self.cfg.fake_lead, self.cfg.initial_fit_score,
            )
            self.buffer.add(phone, text)

            if receipt_id:
                await self.greenapi.delete_notification(receipt_id)

    async def _process_ready_buffers(self) -> None:
        for phone in list(self.buffer.pending_phones()):
            if not self.buffer.is_ready(phone):
                continue

            combined = self.buffer.flush(phone)
            if not combined:
                continue

            session = self.sessions.get(phone)
            if not session:
                continue

            # Skip if already handed off
            if session.stage == "HANDOFF":
                print(f"  [SKIP] {phone} — zaten handoff edildi")
                continue

            # Extra guard: skip duplicate inbound content that arrives twice
            # within a short period (e.g. duplicate webhook delivery).
            now = time.time()
            normalized = " ".join(combined.split()).strip().lower()
            if (
                normalized
                and normalized == session.last_processed_user_text
                and (now - session.last_processed_user_ts) < 120
            ):
                print(f"  [SKIP] {phone} — duplicate inbound message ignored")
                continue

            # Store user message with timestamp
            session.add_message("user", combined)
            session.last_processed_user_text = normalized
            session.last_processed_user_ts = now
            print(f"  [PROCESS] {phone} — mesaj #{session.msg_count} (score={session.score})")

            # ── Message analysis + smart extraction trigger ─────────────────
            signal_result, should_extract, trigger_reason = await self.scoring.analyze_message(
                combined, session.messages, session.msg_count,
            )

            print(f"  [SIGNAL] intent={signal_result.intent} "
                  f"value={signal_result.information_value} "
                  f"trigger={should_extract} ({trigger_reason}) "
                  f"source={signal_result.classification_source}")

            # ── Layer 2.5: Conversation end detection ────────────────────────
            force_handoff = False
            conv_ending, conv_end_reason = self.scoring.check_conversation_end(combined)
            if conv_ending and session.msg_count >= 2:
                should_extract = True
                force_handoff = True
                print(f"  [CONV END] {conv_end_reason}")

            # ── Layer 3: Max messages safety net ─────────────────────────────
            if not force_handoff and session.msg_count >= self.cfg.max_messages_before_handoff:
                should_extract = True
                force_handoff = True
                print(f"  [MAX MSG] {session.msg_count} >= {self.cfg.max_messages_before_handoff}")

            # ── Generate AI response (with updated CHAMP context) ─────────────
            system_prompt = self.prompts.build_system_prompt(
                lead_json=session.lead_json,
                champ_json=session.champ_json,
            )

            groq_messages = [{"role": "system", "content": system_prompt}]
            groq_messages += [
                {"role": m["role"], "content": m["content"]}
                for m in session.messages
            ]

            chat_id = f"{phone}@c.us"
            await self.greenapi.send_typing(chat_id, typing_time=20000)

            t0 = time.time()
            try:
                ai_response = await self.groq.chat(groq_messages)
            except Exception as exc:
                print(f"  [ERROR] Groq hatası: {exc}")
                continue
            groq_elapsed = time.time() - t0

            session.add_message("assistant", ai_response)
            print(f"  [AI] → {phone}: {ai_response[:120]}...")

            # Send WhatsApp message (non-blocking — extraction runs after send)
            await self.greenapi.send_split_message(chat_id, ai_response, groq_elapsed=groq_elapsed)

            # ── Run extraction + scoring if triggered ─────────────────────────
            if should_extract:
                print(f"  [EXTRACT] Qualification judge başlatılıyor...")
                result = await self.scoring.extract_and_score(session, force_handoff=force_handoff)

                if result["status"] == "handoff":
                    await self._handle_handoff(
                        session,
                        trigger=result.get("trigger", "unknown"),
                        reason=result.get("reason", ""),
                    )
                elif result["status"] == "scored":
                    threshold = result.get("threshold", 75)
                    print(f"  [STATUS] Score: {result['score']} / Threshold: {threshold} — devam")

            print()  # Visual separator


# ── Entrypoint ───────────────────────────────────────────────────────────────

def main() -> None:
    _acquire_singleton_pid()
    env_path = str(PROJECT_ROOT / ".env.simulator")
    cfg = SimConfig.from_env(env_path)

    # Validasyon
    errors = []
    if not cfg.greenapi_id_instance:
        errors.append("SIM_GREENAPI_ID_INSTANCE boş")
    if not cfg.greenapi_api_token:
        errors.append("SIM_GREENAPI_API_TOKEN boş")
    if not cfg.groq_api_key:
        errors.append("SIM_GROQ_API_KEY (veya GROQ_API_KEY) boş")

    if errors:
        print("HATA — Eksik konfigürasyon:")
        for e in errors:
            print(f"  ✗ {e}")
        print(f"\n{env_path} dosyasını oluşturun veya ortam değişkenlerini ayarlayın.")
        sys.exit(1)

    simulator = PromptSimulator(cfg)

    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        print("\n  Durduruluyor...")
        simulator.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(simulator.run())
    finally:
        _release_singleton_pid()


def _pid_is_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True

        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def _acquire_singleton_pid() -> None:
    current_pid = os.getpid()
    if PID_FILE.exists():
        try:
            previous_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            previous_pid = 0

        if previous_pid and previous_pid != current_pid and _pid_is_alive(previous_pid):
            try:
                os.kill(previous_pid, signal.SIGTERM)
                time.sleep(1.0)
            except OSError:
                pass

    PID_FILE.write_text(str(current_pid), encoding="utf-8")


def _release_singleton_pid() -> None:
    try:
        if PID_FILE.exists():
            recorded = PID_FILE.read_text(encoding="utf-8").strip()
            if recorded == str(os.getpid()):
                PID_FILE.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    main()
