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
Write a short, actionable briefing report for the sales team to read before their phone call.

## Guidelines
- **summary**: 1-2 sentences. Who is this person and what do they want? Include property type, location preference, and purpose if known.
- **score_explanation**: Why this score? Reference specific CHAMP dimensions and form data. Not generic — be specific about what data exists vs. what's missing.
- **key_signals**: Extract 2-3 MOST important signals from the data. Format as actionable observations (e.g., "Budget confirmed at 500K-1M EUR" not "money_score=20"). Only include signals actually present — don't pad.
- **recommended_approach**: How should the salesperson open the call? What topic to lead with? What tone? What to avoid? Be specific to THIS lead.
- **potential_objections**: Only list objections mentioned or strongly implied in conversation/form data. Don't invent generic objections.
- **priority**: high = strong buying intent or high score (75+), medium = engaged but exploring (50-74), low = early stage or cold (<50).

Respond ONLY in the following JSON format:
{{
  "summary": "<1-2 sentence lead summary>",
  "score_explanation": "<why they received this score — specific dimensions>",
  "key_signals": ["<signal 1>", "<signal 2>"],
  "recommended_approach": "<specific phone strategy for THIS lead>",
  "potential_objections": ["<objection from data>"],
  "priority": "<high|medium|low>"
}}"""
