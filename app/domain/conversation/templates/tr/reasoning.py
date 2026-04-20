"""Turkish reasoning report prompt template."""

REASONING_REPORT_TEMPLATE = """Sen bir {industry} sektörü satış analisti olarak çalışıyorsun.
Aşağıdaki müşteri adayını analiz et ve satış ekibine kapsamlı bir brifing raporu hazırla.

## Müşteri Verisi
```json
{lead_context}
```

## Puan Dağılımı (Toplam: {score}/100)
```json
{breakdown_context}
```
{champ_section}

## Görev
Satış ekibinin telefon görüşmesi öncesinde okuyacağı, Türkçe, kısa ve eyleme dönük bir brifing raporu yaz.

## Kurallar
- **summary**: 1-2 cümle. Bu kişi kim, ne arıyor? Mülk tipi, lokasyon ve amaç bilgisi varsa belirt.
- **score_explanation**: Neden bu puan? Hangi CHAMP boyutu güçlü, hangisi eksik? Genel değil, spesifik bilgi ver.
- **key_signals**: Veriden çıkan en önemli 2-3 sinyal. Eyleme dönük gözlem formatında yaz (ör: "Bütçe 500K-1M EUR aralığında teyit edildi", "Yatırım amaçlı bakıyor" — "money_score=20" gibi teknik ifade kullanma). Sadece gerçekten var olanları yaz.
- **recommended_approach**: Satışçı aramayı nasıl açmalı? Hangi konuyla başlamalı? Hangi tonu kullanmalı? Bu lead'e özel ol.
- **potential_objections**: Sadece sohbette/formda belirtilen veya güçlü şekilde ima edilen itirazları yaz. Uydurma.
- **priority**: high = güçlü satın alma niyeti veya yüksek puan (75+), medium = ilgili ama keşif aşamasında (50-74), low = erken aşama veya soğuk (<50).

Yanıtını SADECE aşağıdaki JSON formatında ver:
{{
  "summary": "<1-2 cümle müşteri özeti>",
  "score_explanation": "<spesifik boyutlarla puan açıklaması>",
  "key_signals": ["<sinyal 1>", "<sinyal 2>"],
  "recommended_approach": "<bu lead için özel telefon stratejisi>",
  "potential_objections": ["<veriden çıkan itiraz>"],
  "priority": "<high|medium|low>"
}}"""
