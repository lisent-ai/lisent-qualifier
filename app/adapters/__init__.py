"""
app.adapters — Concrete implementations (adapters) of `app.ports` interfaces.

Hexagonal architecture'ın "dışarıdan içeriye doğru" katmanı. Her adapter bir port
protokolünü implement eder ve somut bir backend / external service / protocol ile
konuşur.

Alt paketler:
    - adapters.tenant    — TenantPort (LisentCRM + Standalone)
    - adapters.ingest    — IngestPort (Phase 1.D.2+)
    - adapters.handoff   — HandoffPort (Phase 1.D.2+)
    - adapters.knowledge — KnowledgePort (Phase 1.D.4)
    - adapters.llm       — LLMPort (Phase 1.D.3)
    - adapters.event     — EventPort (Phase 1.D.5)

Strateji (Phase 1.D): Mevcut `app/infrastructure/*` modülleri korunur, adapter'lar
onları IMPORT EDİP wrap eder. Phase 1.G (regression suite geçtikten) sonra
`app/infrastructure/*` modülleri silinecek.
"""
