"""
WebhookLeadPayload — sadece name + phone zorunlu.
Geri kalan tüm alanlar extra_data olarak JSONB'ye kaydedilir.
Scorer bilinen alanları extra_data'dan okur, bulamazsa 'unknown'/'other' kullanır.
"""
from pydantic import BaseModel, ConfigDict, Field
import uuid


class WebhookLeadPayload(BaseModel):
    model_config = ConfigDict(strict=False, extra="allow")

    # Zorunlu
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=5, max_length=30)

    # Opsiyonel — varsa scoring'de kullanılır
    lead_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    def extra_data(self) -> dict:
        """name, phone, lead_id dışındaki tüm alanları döner."""
        known = {"name", "phone", "lead_id"}
        return {k: v for k, v in self.model_dump().items() if k not in known} | (self.model_extra or {})
