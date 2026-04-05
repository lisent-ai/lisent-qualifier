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

Yanıtını SADECE aşağıdaki JSON formatında ver:
{{
  "summary": "<1-2 cümle müşteri özeti>",
  "score_explanation": "<neden bu puanı aldı>",
  "key_signals": ["<sinyal 1>", "<sinyal 2>", "<sinyal 3>"],
  "recommended_approach": "<telefonda nasıl yaklaşılmalı>",
  "potential_objections": ["<itiraz 1>", "<itiraz 2>"],
  "priority": "<high|medium|low>"
}}"""
