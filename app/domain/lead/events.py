from dataclasses import dataclass
from datetime import datetime

from .entities import Lead


@dataclass(frozen=True)
class LeadReceived:
    lead: Lead
    occurred_at: datetime


@dataclass(frozen=True)
class LeadQualified:
    lead: Lead
    score: int
    occurred_at: datetime
