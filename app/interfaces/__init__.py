"""
app.interfaces — Deployment shape entry points.

Her interface bir "kapı" — aynı core'a farklı protokol/format/auth üzerinden
erişim sağlar. Phase 1.E'de temel scaffold:

    - rest_public/ — Public v1 REST API (external tenants için)
    - rest_internal/ (Phase 1.D'de placeholder) — mevcut /webhook/lead/{token}
      + /internal/leads/* routes'ları burada barınacak (Phase 1.F sonrası taşınır)
    - oauth/ (Phase 2) — OAuth2 Authorization Server
    - mcp/ (Phase 5) — MCP Server
"""
