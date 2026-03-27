from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerHandoffCommand:
    session_id: str
