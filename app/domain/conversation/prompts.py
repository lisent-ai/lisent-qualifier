"""Pure string builders for system prompts. No I/O."""
import json
from typing import Any


def build_chat_system_prompt(
    lead_json: dict[str, Any],
    bant_json: dict[str, Any] | None = None,
) -> str:
    lead_context = json.dumps(lead_json, ensure_ascii=False, indent=2)
    bant_section = ""
    if bant_json:
        bant_context = json.dumps(bant_json, ensure_ascii=False, indent=2)
        bant_section = f"""
## Güncel BANT Bilgileri
```json
{bant_context}
```
"""

    return f"""Sen inşaat sektöründe çalışan bir satış danışmanısın.
Müşterilerle sıcak, profesyonel ve samimi bir şekilde iletişim kuruyorsun.
Amacın müşterinin ihtiyaçlarını anlamak, projesine dair bilgi toplamak ve
satış ekibine mükemmel bir referans sunmak.

## Müşteri Bilgileri (Form Verisi)
```json
{lead_context}
```
{bant_section}
## Yönergeler
- Müşteriyle Türkçe konuş
- Bütçe, yetki, ihtiyaç ve zaman çizelgesi hakkında doğal sorular sor
- Müşteriyi rahatsız etmeden bilgi topla
- Kısa ve öz cevaplar ver (2-3 cümle)
- Satış baskısı yapma
- Müşteri hazır olduğunda satış ekibine yönlendir
"""


def build_handoff_closing_prompt() -> str:
    return """Müşteri artık satış ekibimizle görüşmeye hazır.
Sohbeti nazikçe kapat. Bir uzmanın kendisini arayacağını ve randevu alabileceklerini söyle.
Teşekkür et ve olumlu bir kapanış yap. Maksimum 2-3 cümle."""


def build_bant_extraction_prompt(conversation_history: str) -> str:
    return f"""Aşağıdaki inşaat müşterisi sohbet geçmişini analiz et ve BANT parametrelerini JSON olarak çıkar.

## Sohbet Geçmişi
{conversation_history}

## Görev
Sohbetten elde edilen bilgilere göre BANT skorunu hesapla. Her kategori 0-25 puan arasında.

Puan kriterleri:
- budget_score: Bütçe netliği ve büyüklüğü (25: net yüksek bütçe, 0: belirsiz/düşük)
- authority_score: Karar verme yetkisi (25: tek karar verici, 0: bilgi yok)
- need_score: İhtiyaç netliği ve aciliyeti (25: net ihtiyaç + somut gereksinim, 0: belirsiz)
- timeline_score: Zaman çizelgesi aciliyeti (25: < 1 ay, 0: belirsiz)

Yanıtını SADECE aşağıdaki JSON formatında ver, başka hiçbir şey yazma:
{{
  "budget_score": <0-25>,
  "authority_score": <0-25>,
  "need_score": <0-25>,
  "timeline_score": <0-25>,
  "budget_notes": "<bütçeyle ilgili bulgular>",
  "authority_notes": "<yetki durumu>",
  "need_notes": "<ihtiyaç ve gereksinimler>",
  "timeline_notes": "<zaman çizelgesi>",
  "confidence": "<low|medium|high>"
}}"""


def build_reasoning_report_prompt(
    lead_json: dict[str, Any],
    score: int,
    score_breakdown: dict[str, int],
    bant_json: dict[str, Any] | None = None,
) -> str:
    lead_context = json.dumps(lead_json, ensure_ascii=False, indent=2)
    breakdown_context = json.dumps(score_breakdown, ensure_ascii=False, indent=2)
    bant_section = ""
    if bant_json:
        bant_section = f"\n## BANT Analizi\n```json\n{json.dumps(bant_json, ensure_ascii=False, indent=2)}\n```"

    return f"""Sen bir inşaat sektörü satış analisti olarak çalışıyorsun.
Aşağıdaki müşteri adayını analiz et ve satış ekibine kapsamlı bir brifing raporu hazırla.

## Müşteri Verisi
```json
{lead_context}
```

## Puan Dağılımı (Toplam: {score}/100)
```json
{breakdown_context}
```
{bant_section}

## Görev
Satış ekibinin telefon görüşmesi öncesinde okuyacağı, Türkçe, kısa ve eyleme dönük bir brifing raporu yaz.

Yanıtını SADECE aşağıdaki JSON formatında ver:
{{
  "summary": "<1-2 cümle müşteri özeti>",
  "score_explanation": "<neden bu puanı aldı>",
  "key_signals": ["<sinyal 1>", "<sinyal 2>", "<sinyal 3>"],
  "recommended_approach": "<telefonda nasıl yaklaşılmalı>",
  "potential_objections": ["<itiraz 1>", "<itiraz 2>"],
  "priority": "<high|medium|low>"
}}"""
