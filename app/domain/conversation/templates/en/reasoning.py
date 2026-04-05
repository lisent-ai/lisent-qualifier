"""English reasoning report prompt template."""

REASONING_REPORT_TEMPLATE = """You are a {industry} sector sales analyst.
Analyze the following lead and prepare a comprehensive briefing report for the sales team.

## Lead Data
```json
{lead_context}
```

## Score Breakdown (Total: {score}/100)
```json
{breakdown_context}
```
{champ_section}

## Task
Write a short, actionable briefing report for the sales team to read before their call.

Respond ONLY in the following JSON format:
{{
  "summary": "<1-2 sentence lead summary>",
  "score_explanation": "<why they received this score>",
  "key_signals": ["<signal 1>", "<signal 2>", "<signal 3>"],
  "recommended_approach": "<how to approach them on the phone>",
  "potential_objections": ["<objection 1>", "<objection 2>"],
  "priority": "<high|medium|low>"
}}"""
