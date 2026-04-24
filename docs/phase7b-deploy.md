# Phase 7.B Deploy — CRM Webhook Listener

Hedef: qualifier'ın pre-score sonucunu CRM'e **REST PATCH** (pull) yerine
**webhook push** ile iletmesi. İki repo birlikte deploy edilir:

1. **lisent.ai-CRM-service** — yeni inbound endpoint `POST /internal/webhooks/qualifier/pre-score`
2. **lisent-qualifier** — yeni outbound fan-out client, dual-write flag ile kontrollü

Sıra: **CRM önce, qualifier sonra.** CRM listener canlı değilken qualifier
fan-out'u aktif etmek sadece `401`/`503` loglar — veri kaybı olmaz ama loglara
gürültü girer.

## 0. Pre-deploy checklist

- [ ] **Shared secret üret** (32+ byte random hex):
  ```bash
  openssl rand -hex 32
  # kopyala — hem CRM hem qualifier env'ine aynı değer yazılır
  ```
- [ ] CRM repo'da `go test ./internal/leads/ -run 'TestVerifyQualifier|TestClampScore|TestBuildBreakdownFromEvent|TestParseEventTimestamp'` yeşil
- [ ] Qualifier'da `.venv_test/bin/pytest tests/unit/infrastructure/test_crm_internal_webhook_client.py -v` yeşil
- [ ] DB backup (qualifier + CRM)

## 1. CRM deploy (listener canlı, flag default kapalı)

### 1.1 Dokploy env — **lisent-crm-service** app

Panel → Environment → Add:

```
QUALIFIER_WEBHOOK_SECRET=<openssl rand çıktısı>
```

### 1.2 Redeploy

Dokploy push → deploy. Health check (host'tan, qualifier container içinden
`curl` ile — internal DNS host'tan resolve olmaz):

```bash
# Endpoint var mı (400 bekleniyor — HMAC yok):
docker exec lisent-qualifier-core-service-wksi7k-ai-lead-qualifier-1 \
  curl -sS -X POST http://lisent-ai-crm-service-dwxhdp:9090/internal/webhooks/qualifier/pre-score \
  -H 'Content-Type: application/json' -d '{}'
# → {"error":"missing timestamp"}

# Secret configure değilse (env'de boşsa) → {"error":"qualifier webhook secret not configured"} (503)
```

### 1.3 Smoke (gerçek event manual)

Qualifier container'ının içinden — aynı dokploy-network'te:

```bash
docker exec -it lisent-qualifier-core-service-wksi7k-ai-lead-qualifier-1 sh -c '
SECRET="<same-secret>"
BODY='\''{"event_type":"pre_score.judged","event_id":"smoke-1","tenant_id":"x","lead_id":"x","crm_lead_id":"<existing-crm-lead-uuid>","score":85,"threshold":75,"path":"fast","timestamp":"2026-04-24T12:00:00Z","payload":{}}'\''
TS=$(date +%s%3N)
SIG=$(printf "%s" "$TS.$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk "{print \$2}")

curl -sS -X POST http://lisent-ai-crm-service-dwxhdp:9090/internal/webhooks/qualifier/pre-score \
  -H "Content-Type: application/json" \
  -H "X-Lisent-Timestamp: $TS" \
  -H "X-Lisent-Signature: sha256=$SIG" \
  -H "X-Lisent-Event-Id: smoke-1" \
  -d "$BODY"
'
# → {"status":"accepted","lead_id":"...","score":85,"path":"fast","event_id":"smoke-1"}
```

CRM panel'de o lead'in `ai_score=85`, `ai_path=fast` olması gerekir.

## 2. Qualifier deploy (dual-write — hala REST + yeni webhook)

### 2.1 Dokploy env — **lisent-qualifier-core-service** app

Panel → Environment → Add:

```
CRM_INTERNAL_WEBHOOK_URL=http://lisent-ai-crm-service-dwxhdp:9090/internal/webhooks/qualifier/pre-score
CRM_INTERNAL_WEBHOOK_SECRET=<same-secret>
CRM_WEBHOOK_LISTENER_ENABLED=true
PRESCORE_CRM_REST_ENABLED=true     # DUAL-WRITE — her iki kanal aktif
```

### 2.2 Redeploy

Dokploy push → deploy. İlk lead geldiğinde log'da:

```
crm_internal_webhook_sent  url=... event_id=pre-score-<crm-lead-uuid> score=... path=...
crm_writethrough_ai_metadata_success  (veya benzeri)
```

İki kanal da 200 dönmeli. Idempotency anahtarları farklı:
- Webhook: `event_id=pre-score-<crm_lead_id>` → CRM `idempotency_keys` tablosu
- REST:    `idempotency_key=lead-pre-score-<crm_lead_id>` → aynı tablo, farklı path

Birbirine müdahale etmez; CRM DB sadece son yazanın ai_score'unu görür.

### 2.3 Observation

**Minimum 1 hafta, ideal 2 hafta** dual-write döneminde:

- `crm_internal_webhook_sent` count vs `crm_writethrough_ai_metadata_success` count eşit olmalı
- `crm_internal_webhook_failed` rate < %1
- CRM panel'de ai_score update'leri webhook (~1s) veya REST (~2s) — fark
  görünmüyor olmalı (her ikisi idempotent; son yazan kazanır)

Webhook-only mode'a geçmek için gün X'te:

```
PRESCORE_CRM_REST_ENABLED=false
```

## 3. Cutover (webhook-only)

1-2 hafta sonra, gözlem temiz ise:

```
# Dokploy qualifier env
PRESCORE_CRM_REST_ENABLED=false
```

Redeploy. Artık `_push_crm_ai_metadata` sadece webhook kanalını çalıştırır.
REST `try_update_ai_metadata` kod path'i — silinmez (chat extractor / qualification
handler hâlâ kullanabiliyor), ama prescore bu path'e uğramaz.

## Rollback

**Webhook flaky görünüyorsa** (1 dk):

```
# Dokploy qualifier env
CRM_WEBHOOK_LISTENER_ENABLED=false
# PRESCORE_CRM_REST_ENABLED=true (zaten)
```

Redeploy. Sadece REST PATCH çalışır — Phase 7 pre-rollout davranışı.

**CRM listener kırılırsa** (ör. bad migration, panel çökmesi):

```
# Qualifier tarafı aynı — webhook'u kapat:
CRM_WEBHOOK_LISTENER_ENABLED=false
```

CRM endpoint'i kendi repo'sundan revert ile geri alınır; shared secret env var
kalırsa sorun olmaz (listener yoksa 404 döner, kimse fark etmez).

**Cutover sonrası REST'i geri açma gereği**:

```
PRESCORE_CRM_REST_ENABLED=true
```

## Değişen dosyalar

**lisent.ai-CRM-service**:
- `internal/leads/qualifier_webhook.go` (yeni)
- `internal/leads/qualifier_webhook_test.go` (yeni)
- `internal/config/config.go` — `QualifierWebhookSecret` eklendi
- `cmd/api/main.go` — `leads.RegisterQualifierWebhookRoutes` çağrısı

**lisent-qualifier**:
- `app/infrastructure/crm/internal_webhook_client.py` (yeni)
- `tests/unit/infrastructure/test_crm_internal_webhook_client.py` (yeni)
- `app/application/scoring/pre_score_service.py` — `_push_crm_ai_metadata` dual-write'a dönüştürüldü
- `app/config.py` — 5 yeni ayar (url, secret, listener_enabled, rest_enabled, timeout)
- `docker-compose.yml` — interpolation bloğu

## Known gotchas

- **Replay window**: timestamp ±5 dk dışındaysa 401. Sunucu saat kaymasına
  duyarlı; CRM ve qualifier NTP sync olmalı.
- **Idempotency anahtarı**: `event_id=pre-score-{crm_lead_id}` — aynı lead
  tekrar score edilirse (worker retry) idempotent no-op. Eğer bir lead kasıtlı
  olarak yeniden skorlanmak istenirse farklı event_id gerekir (şu an worker
  otomatik yapmıyor).
- **4xx no-retry**: CRM 401 dönerse (secret mismatch) qualifier log'lar ama
  retry etmez. 5xx retry edilir (3×, exp backoff). Secret mismatch'i
  erken yakalamak için 2.3 observation'ında `crm_internal_webhook_failed` rate
  izlenmeli.
