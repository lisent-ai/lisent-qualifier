"""StubOSINTAdapter — boş profile döner.

Kullanım alanları:
    - `OSINT_ENABLED=false` (feature flag kapalı)
    - Unit/integration testleri
    - Tenant'ın OSINT'i explicit kapattığı durumlar

Deterministik, sıfır I/O. `cache_hit=False`, `provider="stub"`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.ports.osint import OSINTPort, OSINTProfile


class StubOSINTAdapter(OSINTPort):
    @property
    def name(self) -> str:
        return "stub"

    async def enrich(
        self,
        *,
        email: str | None,
        phone: str | None,
        name: str | None = None,
        tenant_id: UUID | None = None,
    ) -> OSINTProfile:
        notes: list[str] = ["osint disabled (stub adapter)"]
        if not email and not phone:
            notes.append("no email or phone provided")
        return OSINTProfile(
            provider=self.name,
            fetched_at=datetime.now(UTC),
            notes=notes,
            elapsed_ms=0,
        )
