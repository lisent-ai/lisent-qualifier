from pydantic import BaseModel, Field
from typing import Literal


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    model: str
    messages: list[LLMMessage]
    temperature: float = 0.1
    max_tokens: int = 1024
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


# ── BANT extraction response ─────────────────────────────────────────────────

class BANTExtractionResult(BaseModel):
    budget_score: int = Field(ge=0, le=25)
    authority_score: int = Field(ge=0, le=25)
    need_score: int = Field(ge=0, le=25)
    timeline_score: int = Field(ge=0, le=25)
    budget_notes: str = ""
    authority_notes: str = ""
    need_notes: str = ""
    timeline_notes: str = ""
    confidence: Literal["low", "medium", "high"] = "low"

    @property
    def total(self) -> int:
        return self.budget_score + self.authority_score + self.need_score + self.timeline_score


# ── Reasoning report response ─────────────────────────────────────────────────

class ReasoningReportResult(BaseModel):
    summary: str
    score_explanation: str
    key_signals: list[str]
    recommended_approach: str
    potential_objections: list[str]
    priority: Literal["high", "medium", "low"]
