#!/usr/bin/env python3
"""
Prompt Simulator — WhatsApp üzerinden AI sohbet akışını simüle eder.

Tüm altyapı bağımlılıkları (CRM, DB, Local LLM, Redis) devre dışı.
Sadece Groq API + GreenAPI ile çalışır.

Kullanım:
  1. .env.simulator dosyasını düzenle
  2. python scripts/prompt_simulator.py
  3. WhatsApp'tan belirtilen numaraya mesaj at
  4. AI otomatik yanıt verir

Prompt geliştirici sadece şu dosyaları düzenler:
  - app/domain/conversation/templates/tr/chat_system.py
  - app/domain/conversation/templates/en/chat_system.py
  - app/domain/conversation/prompts.py
  - app/domain/conversation/few_shots/*.py

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

    # Opsiyonel: company config override'ları
    working_hours: str = ""
    pricing_hints: str = ""
    kb_content: str = ""
    webhook_data_content: str = ""
    forbidden_topics: list = field(default_factory=list)
    faq_entries: list = field(default_factory=list)
    custom_qualifying_questions: list = field(default_factory=list)
    ideal_customer_profile: str = ""
    handoff_aggressiveness: str = "balanced"

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
        cfg.ideal_customer_profile = os.environ.get("SIM_IDEAL_CUSTOMER_PROFILE", "")
        cfg.handoff_aggressiveness = os.environ.get("SIM_HANDOFF_AGGRESSIVENESS", "balanced")

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

        # Fake lead override (JSON string)
        lead_file = os.environ.get("SIM_FAKE_LEAD_FILE")
        if lead_file:
            lead_file_path = Path(lead_file)
            if not lead_file_path.is_absolute() and env_file_path is not None:
                lead_file_path = (env_file_path.parent / lead_file_path).resolve()
            try:
                cfg.fake_lead = json.loads(lead_file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        lead_json = os.environ.get("SIM_FAKE_LEAD_JSON")
        if lead_json:
            try:
                cfg.fake_lead = json.loads(lead_json)
            except json.JSONDecodeError:
                pass

        return cfg


# ── In-Memory Session ───────────────────────────────────────────────────────

@dataclass
class SimSession:
    phone: str
    lead_json: dict[str, Any]
    messages: list[dict[str, str]] = field(default_factory=list)
    msg_count: int = 0
    champ_json: dict | None = None
    created_at: float = field(default_factory=lambda: time.time())


class SessionStore:
    """In-memory session store — Redis yerine."""

    def __init__(self) -> None:
        self._sessions: dict[str, SimSession] = {}  # phone -> session

    def get_or_create(self, phone: str, lead_json: dict) -> SimSession:
        if phone not in self._sessions:
            lead = {**lead_json, "phone": phone}
            self._sessions[phone] = SimSession(phone=phone, lead_json=lead)
            print(f"  [SESSION] Yeni session: {phone}")
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
        """Bir bildirim al (long-polling). Yoksa None."""
        try:
            resp = await self._client.get(self._url("receiveNotification"))
            if resp.status_code == 200:
                data = resp.json()
                return data  # None dönebilir (boş kuyruk)
            return None
        except Exception as exc:
            print(f"  [WARN] GreenAPI receive hatası: {exc}")
            return None

    async def delete_notification(self, receipt_id: int) -> None:
        """İşlenmiş bildirimi sil."""
        try:
            await self._client.delete(
                self._url("deleteNotification") + f"/{receipt_id}"
            )
        except Exception:
            pass

    async def send_message(self, chat_id: str, message: str) -> bool:
        """Mesaj gönder."""
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
        """Karşı tarafa 'yazıyor...' göster."""
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
        """--- separator'ına göre böl, insan benzeri yazım gecikmesiyle gönder."""
        from app.infrastructure.greenapi.client import compute_typing_delay

        parts = ai_response.split("---", 1)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) == 2:
            # Part 1: Groq süresi düşülerek gecikme
            delay_p1 = compute_typing_delay(parts[0])
            remaining = max(0, delay_p1 - groq_elapsed)
            if remaining > 0:
                await asyncio.sleep(remaining)
            await self.send_message(chat_id, parts[0])

            # Part 2: tam yazım gecikmesi
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
        self._client = httpx.AsyncClient(
            timeout=30,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def chat(self, messages: list[dict]) -> str:
        """Non-streaming chat completion."""
        resp = await self._client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def close(self) -> None:
        await self._client.aclose()


# ── Prompt Builder (gerçek projedeki prompt'ları kullanır) ────────────────────

class PromptBuilder:
    """Projenin gerçek prompt builder'ını kullanır — böylece template değişiklikleri anında yansır."""

    def __init__(self, cfg: SimConfig) -> None:
        self.cfg = cfg

    def build_system_prompt(
        self,
        lead_json: dict,
        champ_json: dict | None = None,
    ) -> str:
        from app.domain.conversation.prompts import build_chat_system_prompt

        # Merge KB content with webhook data content
        kb = self.cfg.kb_content
        if self.cfg.webhook_data_content:
            kb = (kb + "\n\n---\n\n" + self.cfg.webhook_data_content) if kb else self.cfg.webhook_data_content

        company_config = {
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

        return build_chat_system_prompt(lead_json, champ_json, company_config)


# ── Debounce Buffer ──────────────────────────────────────────────────────────

class DebounceBuffer:
    """Ardışık mesajları birleştirir (WhatsApp handler'daki gibi)."""

    def __init__(self, debounce_seconds: float) -> None:
        self._debounce = debounce_seconds
        self._buffers: dict[str, list[str]] = {}    # phone -> [texts]
        self._timestamps: dict[str, float] = {}     # phone -> last_msg_time

    def add(self, phone: str, text: str) -> None:
        self._buffers.setdefault(phone, []).append(text)
        self._timestamps[phone] = time.time()

    def is_ready(self, phone: str) -> bool:
        """Debounce süresi doldu mu?"""
        ts = self._timestamps.get(phone)
        if ts is None:
            return False
        return (time.time() - ts) >= self._debounce

    def flush(self, phone: str) -> str | None:
        """Buffer'ı birleştir ve temizle."""
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
        self.prompts = PromptBuilder(cfg)
        self.buffer = DebounceBuffer(cfg.debounce_seconds)
        self._seen: set[str] = set()  # dedup: id_message'ler
        self._running = True

    async def _send_greeting(self, phone: str) -> None:
        """Form gelmiş gibi ilk selamlama mesajını AI üzerinden gönder."""
        session = self.sessions.get_or_create(phone, self.cfg.fake_lead)

        print(f"  [GREETING] {phone} numarasına ilk mesaj gönderiliyor...")

        # System prompt oluştur (conversation history boş → İLK MESAJ KURALI tetiklenir)
        system_prompt = self.prompts.build_system_prompt(
            lead_json=session.lead_json,
            champ_json=None,
        )

        messages = [{"role": "system", "content": system_prompt}]

        # Typing indicator + Groq (max süre — mesaj gönderilince otomatik biter)
        chat_id = f"{phone}@c.us"
        await self.greenapi.send_typing(chat_id, typing_time=20000)

        t0 = time.time()
        try:
            ai_response = await self.groq.chat(messages)
        except Exception as exc:
            print(f"  [ERROR] Groq greeting hatası: {exc}")
            return
        groq_elapsed = time.time() - t0

        # Assistant yanıtını session'a kaydet
        session.messages.append({"role": "assistant", "content": ai_response})

        print(f"  [AI] → {phone}: {ai_response[:120]}...")

        # WhatsApp'a gönder (insan benzeri gecikmeyle)
        await self.greenapi.send_split_message(chat_id, ai_response, groq_elapsed=groq_elapsed)
        print(f"  [GREETING] İlk mesaj gönderildi. Yanıt bekleniyor...\n")

    async def run(self) -> None:
        target_phone = self.cfg.fake_lead.get("phone", "")

        print("=" * 60)
        print("  PROMPT SIMULATOR")
        print("=" * 60)
        print(f"  GreenAPI Instance : {self.cfg.greenapi_id_instance}")
        print(f"  Groq Model        : {self.cfg.groq_model}")
        print(f"  Dil / Sektör      : {self.cfg.language} / {self.cfg.sector}")
        print(f"  Şirket            : {self.cfg.company_name}")
        print(f"  Ton               : {self.cfg.tone}")
        print(f"  Hedef Telefon     : {target_phone or '(yok — pasif mod)'}")
        print(f"  Poll Aralığı      : {self.cfg.poll_interval}s")
        print(f"  Debounce          : {self.cfg.debounce_seconds}s")
        print("=" * 60)

        # Telefon numarası varsa → form gelmiş gibi ilk mesajı gönder
        if target_phone:
            print(f"  Form simülasyonu başlatılıyor → {target_phone}\n")
            await self._send_greeting(target_phone)
        else:
            print("  WhatsApp'tan mesaj bekleniyor... (Ctrl+C ile durdur)\n")

        try:
            while self._running:
                # 1. GreenAPI'den bildirim al
                await self._poll_notifications()

                # 2. Debounce süresi dolan buffer'ları işle
                await self._process_ready_buffers()

                # 3. Bekle
                await asyncio.sleep(self.cfg.poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            await self.greenapi.close()
            await self.groq.close()
            print("\n  Simulator durduruldu.")

    def stop(self) -> None:
        self._running = False

    async def _poll_notifications(self) -> None:
        """GreenAPI'den bildirimleri al ve buffer'a ekle."""
        while True:
            notification = await self.greenapi.receive_notification()
            if notification is None:
                break

            receipt_id = notification.get("receiptId")
            body = notification.get("body", {})
            type_webhook = body.get("typeWebhook", "")

            # Sadece gelen mesajları işle
            if type_webhook != "incomingMessageReceived":
                if receipt_id:
                    await self.greenapi.delete_notification(receipt_id)
                continue

            # Sadece text mesajları
            msg_data = body.get("messageData", {})
            type_message = msg_data.get("typeMessage", "")
            if type_message not in ("textMessage", "extendedTextMessage"):
                if receipt_id:
                    await self.greenapi.delete_notification(receipt_id)
                continue

            # Mesaj bilgilerini çıkar
            id_message = body.get("idMessage", "")
            sender = body.get("senderData", {})
            chat_id = sender.get("chatId", "")
            phone = sender.get("sender", "").replace("@c.us", "")
            sender_name = sender.get("senderName", phone)

            # Text çıkar
            if type_message == "textMessage":
                text = msg_data.get("textMessageData", {}).get("textMessage", "")
            else:
                text = msg_data.get("extendedTextMessageData", {}).get("text", "")

            # Dedup
            if id_message in self._seen:
                if receipt_id:
                    await self.greenapi.delete_notification(receipt_id)
                continue
            self._seen.add(id_message)

            # Grup mesajlarını atla
            if not chat_id.endswith("@c.us"):
                if receipt_id:
                    await self.greenapi.delete_notification(receipt_id)
                continue

            print(f"  [RECV] ← {sender_name} ({phone}): {text}")

            # Session oluştur/getir
            self.sessions.get_or_create(phone, self.cfg.fake_lead)

            # Buffer'a ekle (debounce)
            self.buffer.add(phone, text)

            # Bildirimi sil
            if receipt_id:
                await self.greenapi.delete_notification(receipt_id)

    async def _process_ready_buffers(self) -> None:
        """Debounce süresi dolmuş buffer'ları işle."""
        for phone in list(self.buffer.pending_phones()):
            if not self.buffer.is_ready(phone):
                continue

            combined = self.buffer.flush(phone)
            if not combined:
                continue

            session = self.sessions.get(phone)
            if not session:
                continue

            # Mesajı session'a ekle
            session.messages.append({"role": "user", "content": combined})
            session.msg_count += 1

            print(f"  [PROCESS] {phone} — mesaj #{session.msg_count}")

            # System prompt oluştur (projenin gerçek builder'ı)
            system_prompt = self.prompts.build_system_prompt(
                lead_json=session.lead_json,
                champ_json=session.champ_json,
            )

            # Groq mesaj listesini oluştur
            messages = [{"role": "system", "content": system_prompt}]
            messages += session.messages

            # Typing indicator + Groq (max süre — mesaj gönderilince otomatik biter)
            chat_id = f"{phone}@c.us"
            await self.greenapi.send_typing(chat_id, typing_time=20000)

            t0 = time.time()
            try:
                ai_response = await self.groq.chat(messages)
            except Exception as exc:
                print(f"  [ERROR] Groq hatası: {exc}")
                continue
            groq_elapsed = time.time() - t0

            # Assistant yanıtını session'a kaydet
            session.messages.append({"role": "assistant", "content": ai_response})

            print(f"  [AI] → {phone}: {ai_response[:120]}...")

            # WhatsApp'a gönder (insan benzeri gecikmeyle)
            await self.greenapi.send_split_message(chat_id, ai_response, groq_elapsed=groq_elapsed)


# ── Entrypoint ───────────────────────────────────────────────────────────────

def main() -> None:
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

    # Graceful shutdown
    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        print("\n  Durduruluyor...")
        simulator.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    loop.run_until_complete(simulator.run())


if __name__ == "__main__":
    main()
