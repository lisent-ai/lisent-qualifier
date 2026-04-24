from pydantic import BaseModel, Field
from typing import Any, Literal


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    model: str
    messages: list[LLMMessage]
    temperature: float = 0.1
    max_tokens: int = 2048
    stream: bool = False


class LLMChoice(BaseModel):
    message: LLMMessage
    finish_reason: str | None = None


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    id: str = ""
    choices: list[LLMChoice]
    usage: LLMUsage = Field(default_factory=LLMUsage)

    @property
    def content(self) -> str:
        if self.choices:
            return self.choices[0].message.content
        return ""


# ── CHAMP extraction response ────────────────────────────────────────────────

class CHAMPExtractionResult(BaseModel):
    """Challenges, Authority, Money, Prioritization — 0-25 per dimension."""
    challenges_score: int = Field(ge=0, le=25)
    authority_score: int = Field(ge=0, le=25)
    money_score: int = Field(ge=0, le=25)
    prioritization_score: int = Field(ge=0, le=25)

    challenges_notes: str = ""
    authority_notes: str = ""
    money_notes: str = ""
    prioritization_notes: str = ""

    # Per-dimension confidence (0.0-1.0)
    challenges_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    authority_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    money_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    prioritization_confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    confidence: Literal["low", "medium", "high"] = "low"

    @property
    def total(self) -> int:
        return (
            self.challenges_score
            + self.authority_score
            + self.money_score
            + self.prioritization_score
        )


class SectorQualifiersResult(BaseModel):
    """Sector-specific qualifiers extracted alongside CHAMP."""
    # Construction
    has_land: bool | None = None
    permit_status: str | None = None
    has_architect: bool | None = None
    budget_source: str | None = None
    competing_bids: bool | None = None
    project_sqm: int | None = None
    # Real estate (Cyprus investor)
    has_property_shortlist: int | bool | None = None
    financing_ready: str | None = None
    visit_intent: bool | None = None
    decision_partner_aligned: bool | None = None
    exit_strategy_clear: bool | None = None
    property_type: str | None = None
    location: str | None = None


class MessageAnalysis(BaseModel):
    """Per-message intent and sentiment — piggybacked on CHAMP extraction."""
    intent: Literal[
        "information_seeking",
        "price_inquiry",
        "objection",
        "positive_signal",
        "off_topic",
        "greeting",
        "closing",
        "meeting_request",
        "unknown",
    ] = "unknown"
    sentiment: Literal["positive", "neutral", "negative", "frustrated"] = "neutral"
    buying_signals: list[str] = Field(default_factory=list)


class CHAMPFullExtractionResult(BaseModel):
    """Combined extraction: CHAMP scores + sector qualifiers + message analysis."""
    champ: CHAMPExtractionResult
    sector_qualifiers: SectorQualifiersResult = Field(
        default_factory=SectorQualifiersResult
    )
    message_analysis: MessageAnalysis = Field(default_factory=MessageAnalysis)


# ── Qualification Judge response ──────────────────────────────────────────────

class QualificationJudgmentResult(BaseModel):
    """LLM Qualification Judge output — CoT first, then structured scores."""

    # Chain-of-thought (LLM writes this FIRST — forces thinking before scoring)
    thinking: str = Field(
        default="",
        description="Step-by-step reasoning about lead quality",
    )

    # Per-dimension scores (same 0-25 scale as CHAMP for backward compat)
    challenges_score: int = Field(ge=0, le=25, default=0)
    challenges_reasoning: str = ""
    challenges_confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    authority_score: int = Field(ge=0, le=25, default=0)
    authority_reasoning: str = ""
    authority_confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    money_score: int = Field(ge=0, le=25, default=0)
    money_reasoning: str = ""
    money_confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    prioritization_score: int = Field(ge=0, le=25, default=0)
    prioritization_reasoning: str = ""
    prioritization_confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    # Holistic assessment (LLM's overall judgment)
    holistic_score: int = Field(ge=0, le=100, default=0)
    holistic_reasoning: str = ""
    icp_fit_assessment: str = ""

    # Negative signals (replaces rule-based keyword detection)
    negative_signals: list[str] = Field(default_factory=list)
    negative_penalty: int = Field(le=0, default=0)
    negative_reasoning: str = ""

    # Sector qualifiers (replaces separate extraction)
    sector_qualifiers: SectorQualifiersResult = Field(
        default_factory=SectorQualifiersResult,
    )

    # Handoff decision (Layer 2 — judge decides if lead is ready for sales)
    handoff_ready: bool = False
    handoff_reason: str = ""

    # CTA routing hint — advisory signal for the CTA router. The router has
    # final say, but the judge gives the LLM's best read of which call-to-action
    # suits the lead.
    cta_recommendation: Literal["cyprus_visit", "calendly", "nurture", ""] = ""

    # Next question recommendation (replaces gap routing heuristic)
    missing_info: list[str] = Field(default_factory=list)
    recommended_next_question: str = ""

    # Overall confidence
    confidence: Literal["low", "medium", "high"] = "low"

    # Lead field enrichment — fill empty/unknown fields from conversation
    extracted_budget_range: str = Field(
        default="",
        description="Budget range derived from conversation: under_500k|500k_1m|1m_3m|3m_10m|over_10m",
    )
    extracted_budget_amount: int | None = Field(
        default=None,
        description="Budget amount in TL derived from conversation",
    )
    extracted_project_type: str = Field(
        default="",
        description="Project type: residential|commercial|industrial|renovation|land",
    )
    extracted_timeline_urgency: str = Field(
        default="",
        description="Timeline: immediate|short|medium|long",
    )
    extracted_decision_authority: str = Field(
        default="",
        description="Authority: sole|joint|influencer",
    )
    extracted_city: str = Field(
        default="",
        description="City/location mentioned in conversation",
    )
    extracted_project_details: str = Field(
        default="",
        description="Brief project description (e.g. '3 katlı otel, 40 oda, havuzlu')",
    )

    @property
    def total(self) -> int:
        return (
            self.challenges_score
            + self.authority_score
            + self.money_score
            + self.prioritization_score
        )


# ── Reasoning report response ─────────────────────────────────────────────────

class ReasoningReportResult(BaseModel):
    summary: str
    score_explanation: str
    key_signals: list[str]
    recommended_approach: str
    potential_objections: list[str]
    priority: Literal["high", "medium", "low"]


# ── Field mapping response ───────────────────────────────────────────────────

class FieldMappingResult(BaseModel):
    """LLM/heuristic field mapping sonucu — webhook payload'ından extract edilen alanlar."""
    full_name: str = ""
    phone: str = ""
    email: str = ""
    city: str = ""
    source: str = ""
    project_type: str = ""
    budget_range: str = ""
    budget_amount: int | None = None
    decision_authority: str = ""
    timeline_urgency: str = ""
    notes: str = ""
    external_id: str = ""
    extra_fields: dict[str, Any] = Field(default_factory=dict)
    # Phase 9.4 — intake hygiene
    # How confident the mapper is that the payload's fields were identified
    # correctly. Heuristic path computes from field-filled count; LLM path
    # accepts an explicit confidence in the JSON response if supplied.
    mapping_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    # Human-readable flags discovered during intake (low confidence,
    # duplicate phone, payload suspicious, etc.). Propagated into
    # qualifier_leads.extra_data and then into the scoring prompt so
    # sales_context can warn the caller.
    quality_flags: list[str] = Field(default_factory=list)
