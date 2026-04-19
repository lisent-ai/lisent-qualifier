# ai-lead-qualifier — Dokploy Production Deployment

Bu runbook, `ai-lead-qualifier` servisini Dokploy üzerinde prod'a almak için adım-adım prosedürü içerir. Lisent'in `crm-service` ve `crm-web` servisleri için kullanılan Dokploy pattern'ini takip eder.

## Topology

```
Dokploy project: lisent-prod
├── crm-service           (var)
├── crm-web               (var)
├── qualifier-postgres    pgvector/pgvector:pg17, DB: lead_qualifier
├── qualifier-redis       redis:7.4-alpine
├── qualifier-migrate     on-demand one-shot migration runner
└── ai-lead-qualifier     app, domain: qualifier.lisent.ai, port 8000 (internal)
```

Tüm servisler `dokploy-network` external network'ünde. Kendi aralarında Dokploy'un atadığı internal DNS hostname'leri ile konuşurlar.

## Ön koşullar

- Dokploy'a SSH erişimi
- Dokploy host'unda native `llama.cpp` çalışıyor (port 8080)
  - Kontrol: `curl -fsS http://127.0.0.1:8080/v1/models`
  - Çalışmıyorsa: `./scripts/start_local_llm.sh`
- Groq API key
- CRM service zaten deploy edilmiş ve internal DNS'i biliniyor

## Adım 1 — Postgres servisini oluştur

Dokploy → Create Service → Application → Compose, name: **qualifier-postgres**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: lead_qualifier
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
    restart: unless-stopped
    networks:
      - dokploy-network
volumes:
  pg_data:
networks:
  dokploy-network:
    external: true
```

- Env: `POSTGRES_PASSWORD` → `openssl rand -hex 24`
- Deploy → internal DNS adını kaydet (örn: `qualifier-postgres-xyz123`).

## Adım 2 — Redis servisini oluştur

Yeni Application → Compose, name: **qualifier-redis**

```yaml
services:
  redis:
    image: redis:7.4-alpine
    command: >
      redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru
      --appendonly yes --appendfsync everysec
    volumes:
      - redis_data:/data
    restart: unless-stopped
    networks:
      - dokploy-network
volumes:
  redis_data:
networks:
  dokploy-network:
    external: true
```

Deploy → internal DNS adını kaydet.

## Adım 3 — Local LLM kontrolü

```bash
ssh <dokploy-host>
curl -fsS http://127.0.0.1:8080/v1/models
# 200 dönmüyorsa qualifier'ı başlatmadan önce:
cd /opt/ai-lead-qualifier && ./scripts/start_local_llm.sh
```

**Not:** Host reboot senaryosu için `scripts/start_local_llm.sh`'yi `@reboot` cron veya systemd unit'e bağla.

## Adım 4 — Migration (bir kez)

Yeni Application → Git source (ai-lead-qualifier repo), compose path: `docker-compose.migrate.yml`, name: **qualifier-migrate**.

Environment:
```
DATABASE_URL=postgresql://app:<pg-pass>@qualifier-postgres-xyz123:5432/lead_qualifier
```

Deploy. Container çalışır → biter. Log'da `[migrate] done (N applied)` gör.

Doğrulama:
```bash
# Dokploy host'unda veya pgAdmin ile:
psql "$DATABASE_URL" -c "SELECT version, applied_at FROM schema_migrations ORDER BY version;"
```

Beklenen tablolar: `qualifier_leads`, `qualifier_sessions`, `qualifier_handoffs`, `handoff_attempts`, `activity_log`, `ai_kb_documents`, `schema_migrations`.

## Adım 5 — ai-lead-qualifier uygulamasını oluştur

Yeni Application → Git source, compose path: `docker-compose.yml`, name: **ai-lead-qualifier**.

**Domain:** Dokploy UI → Domains → Add:
- Host: `qualifier.lisent.ai`
- Port: `8000`
- HTTPS + Let's Encrypt: enabled

**Environment variables:**

```
# Core
APP_ENV=production
LOG_LEVEL=INFO

# Data stores
DATABASE_URL=postgresql://app:<pg-pass>@qualifier-postgres-xyz123:5432/lead_qualifier
REDIS_DSN=redis://qualifier-redis-xyz456:6379/0

# Groq
GROQ_API_KEY=<groq-key>

# CRM (internal REST)
CRM_BASE_URL=http://crm-service-<existing-hash>:9090
CRM_API_KEY=<match crm-service API_KEY>

# CRM webhook handoff (optional, only if writing to external CRM)
CRM_WEBHOOK_URL=
CRM_WEBHOOK_TOKEN=

# Internal auth
INTERNAL_API_KEY=<openssl rand -hex 32>
WHATSAPP_WEBHOOK_TOKEN=<openssl rand -hex 24>

# Flags
QUALIFIER_CRM_WRITETHROUGH_ENABLED=true
QUALIFIER_LEGACY_API_ENABLED=true
RAG_WEBHOOK_ENABLED=false
```

Deploy → internal DNS adını kaydet (örn. `ai-lead-qualifier-abc789`).

## Adım 6 — CRM servisinin env'ini güncelle

Dokploy → `crm-service` → Environment:

```
AI_QUALIFIER_BASE_URL=http://ai-lead-qualifier-abc789:8000
```

Redeploy.

## Adım 7 — Smoke test

```bash
# Public health
curl -fsS https://qualifier.lisent.ai/health
curl -fsS https://qualifier.lisent.ai/ready

# Prometheus metrics
curl -fsS https://qualifier.lisent.ai/metrics | head -20

# Webhook (company_token CRM'de önceden kayıtlı olmalı)
curl -X POST https://qualifier.lisent.ai/webhook/lead/<company_token> \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","phone":"+905551234567","project_type":"villa","budget_range":"1M-5M"}'
```

Container içinden local LLM erişimi:
```bash
docker exec -it ai-lead-qualifier-<hash> curl -fsS http://host.docker.internal:8080/v1/models
```

## Adım 8 — Webhook sağlayıcılarını yönlendir

- Form sağlayıcıları → `POST https://qualifier.lisent.ai/webhook/lead/{company_token}`
- GreenAPI dashboard → webhook URL → `https://qualifier.lisent.ai/webhook/whatsapp?token=<WHATSAPP_WEBHOOK_TOKEN>`

## Rollback

- **App:** Dokploy UI → previous deployment'a dön.
- **DB:** Migration'lar additive (down yok). Kırıcı migration → Dokploy backup'tan restore + app'i önceki imaja çek.
- **CRM entegrasyonunu devre dışı bırak:** CRM env'de `AI_QUALIFIER_BASE_URL=` boş bırak + redeploy. Qualifier tarafında `CRM_BASE_URL=` boşsa client graceful çalışır (`app/config.py:40`).

## Secret rotation

`INTERNAL_API_KEY` ve `CRM_API_KEY` qualifier + CRM servisinde **aynı anda** güncellenmeli. Sadece birini rotate ederseniz auth kırılır. Rotation sırası:

1. Yeni değeri üret (`openssl rand -hex 32`).
2. Qualifier env'ini güncelle, deploy.
3. CRM env'ini güncelle, deploy.
4. Smoke test: `curl -H "X-API-KEY: <new>" https://qualifier.lisent.ai/internal/leads/<company_id>`

## Operasyonel notlar

- **Prometheus scrape:** `/metrics` endpoint'ini Grafana'ya bağla. Alert hedefleri: `groq_circuit_state`, `crm_webhook_failures_total`, `champ_extraction_errors_total`.
- **Log:** stdout'a JSON structured log (structlog). Dokploy log viewer'dan izle.
- **Graceful shutdown:** `tini` PID 1 olarak SIGTERM'i uvicorn'a iletiyor; Dokploy redeploy sırasında inflight request'ler drain oluyor.
- **Host reboot:** llama.cpp otomatik başlamaz — systemd unit veya `@reboot` cron şart.
