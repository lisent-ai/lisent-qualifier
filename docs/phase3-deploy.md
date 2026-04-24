# Phase 3 Deploy — OSINT + Pre-Scoring Ensemble

Hedef: yeni pre-scoring pipeline'ını (self-hosted OSINT + 3-persona LLM judge
ensemble + deterministic composer audit) prod'a deploy etmek. Tüm yeni
infrastructure **gated off** olarak yayına alınır; shadow observation sonrası
flag flip ile aktif edilir.

## 0. Pre-deploy checklist

- [ ] **Backup** — qualifier DB + Redis snapshot
  ```bash
  ssh kaan@lisent-ai
  docker exec lisent-qualifier-db-tzobyr pg_dump -U app -Fc lead_qualifier \
    > ~/backups/qualifier-prod-pre-phase3-$(date +%Y%m%d-%H%M).sql.gz
  # Redis — bgsave + kopya
  docker exec lisent-qualifier-redis-h1lcug redis-cli BGSAVE
  ```
- [ ] **Unit tests yeşil** — `pytest tests/unit/ -q` (106/106)
- [ ] **Phase 3 integration yeşil** — `pytest tests/integration/test_phase3_prescore_pipeline.py -q` (8/8)
- [ ] **Dokploy panel erişimi hazır** (migration auto-apply için)
- [ ] **Groq API key prod env'de mevcut** (qualification_judge_model zaten kullanıyor)

## 1. OSINT stack deploy (Dokploy)

Ayrı bir Dokploy app olarak `lisent-osint-stack` oluştur.

### 1.1. Dokploy panel setup

1. **Create App → Compose**
2. Name: `lisent-osint-stack`
3. Source: GitHub `lisent-ai/lisent-qualifier` branch `main`
4. **Compose path**: `infra/osint/docker-compose.prod.yml` ← prod compose
5. Build → Auto-deploy on push: evet

Prod compose dosyası lokal versiyonundan farklı olarak:
- Host port binding YOK (sadece `dokploy-network` üzerinden erişim)
- `dokploy-network` external — qualifier container'dan `phoneinfoga:5000` ve
  `holehe-api:8000` DNS alias'larıyla erişilebilir
- Container-native port'lar: phoneinfoga 5000, holehe-api 8000

### 1.3. Deploy + sanity

```bash
# Dokploy panel → "Deploy"
# Bekle — container healthchecks OK olmalı

# Qualifier container'dan internal check
docker exec lisent-qualifier-core-service-wksi7k-ai-lead-qualifier-1 sh -c \
  "curl -sS http://phoneinfoga:5000/api/ | head -c 100"
# → {"success":true,"version":"v2.11.0",...}

docker exec lisent-qualifier-core-service-wksi7k-ai-lead-qualifier-1 sh -c \
  "curl -sS http://holehe-api:8000/health | head -c 100"
# → {"status":"ok","module_count":121,...}
```

**Eğer bu iki check başarısızsa → Phase 6 durur, Dokploy panel'den
troubleshoot, sorun çözüldükten sonra devam et.**

## 2. Migration 004 apply

Dokploy qualifier deploy'unda `/app/entrypoint.sh` veya migration script
varsa auto-apply eder. Manuel uygula:

```bash
docker exec -i lisent-qualifier-db-tzobyr psql -U app -d lead_qualifier \
  < /home/kaan/development-main/lisent-qualifier/scripts/migrations/004_osint_profiles.sql
```

Doğrula:
```bash
docker exec lisent-qualifier-db-tzobyr psql -U app -d lead_qualifier -c \
  "\d+ qualifier_osint_profiles" | head -25
# FORCE RLS enabled, RLS policies (tenant_isolation_osint, super_admin_bypass_osint),
# trigger trg_osint_profiles_updated_at set
```

## 3. Qualifier deploy (gated OFF)

### 3.1. Dokploy Environment Variables

`lisent-qualifier-core-service` app'inde:

**Yeni env vars (Phase 3)**:
- `PRESCORE_WORKER_ENABLED=false`    ← deploy'da off kalsın
- `OSINT_ENABLED=false`               ← stub adapter (zero upstream)
- `OSINT_PHONEINFOGA_URL=http://phoneinfoga:5000`
- `OSINT_HOLEHE_URL=http://holehe-api:8000`
- `OSINT_PROFILE_STALE_DAYS=60`
- `PRE_SCORE_JUDGE_MAX_TOKENS=2048`
- `PRE_SCORE_JUDGE_TIMEOUT=12`
- `PRE_SCORE_DIVERGENCE_THRESHOLD=20`

### 3.2. Deploy

1. GitHub `main`'a merge → Dokploy auto-deploy
2. Container restart + health check
3. Log kontrolü:
   ```bash
   docker logs lisent-qualifier-core-service-wksi7k-ai-lead-qualifier-1 --tail 50 | grep -E "startup|prescore|osint"
   # → "prescore_worker_start_failed" OR "prescore_worker_disabled" görülmeli
   # Worker start edilmiyor çünkü flag off → beklenen durum.
   ```

### 3.3. Regression sanity

Legacy flow:
```bash
# Webhook intake
curl -X POST "https://qualifier.lisent.ai/webhook/lead/<COMPANY_TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"lead_id":"regression-1","name":"Test","phone":"+905551234567","source":"test"}'
# → 200, legacy scoring çalışıyor
```

Public v1:
```bash
curl -X POST "https://qualifier.lisent.ai/v1/leads" \
  -H "Authorization: Bearer <TENANT_API_KEY>" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Test","email":"test@example.com"}'
# → 201, score=0 (enqueue flag off, normal)
```

Phase 4'te `PRESCORE_WORKER_ENABLED=false` default → enqueue skipped, worker
idle. Legacy hiçbir şekilde etkilenmez.

## 4. Shadow mode activation — Stage 1: stub OSINT

Önce **stub OSINT + LLM judge**'i aç. Stub adapter upstream'e dokunmaz, sadece
form verisiyle LLM judge çalışır:

```
Dokploy Environment:
  PRESCORE_WORKER_ENABLED = true
  OSINT_ENABLED           = false   ← STUB kalsın
```

Qualifier restart → worker devreye girer.

### 4.1. Synthetic lead

```bash
# Test tenant'ı kullan (production traffic'e karıştırma)
TEST_TENANT_API_KEY="sk_live_test_..."

curl -X POST "https://qualifier.lisent.ai/v1/leads" \
  -H "Authorization: Bearer $TEST_TENANT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"external_ref":"shadow-stage1-1","name":"Ayşe Yılmaz","email":"ayse@onurinsaat.com.tr","phone":"+905321112233","city":"Ankara","project_type":"commercial","budget_range":"10m_plus","notes":"Ankara Çankaya 15M TL plaza projesi, Eylülde başlamak istiyoruz, karar verici benim."}'
# → 201 {id: <UUID>, score: 0, ...}
LEAD_ID="<UUID_from_response>"
```

### 4.2. 5-10 saniye bekle, skor kontrolü

```bash
sleep 8
curl -sS "https://qualifier.lisent.ai/v1/leads/$LEAD_ID/score" \
  -H "Authorization: Bearer $TEST_TENANT_API_KEY" | jq .
```

**Beklenen çıktı**:
```json
{
  "score": 78,           // ensemble'dan gelen; tam değer LLM'e bağlı
  "threshold": 75,
  "status": "new",
  "path": null,
  "explanation": {
    "components": {...},
    "champ": {...},
    "recommendation": {...},
    "pre_score_ensemble": {
      "median_direct_score": 78,
      "formula_audit_score": 72,
      "divergent": false,
      "persona_scores": {"skeptic": 74, "neutral": 78, "opportunity": 82},
      "signals": {
        "identity": {...},
        "intent": {...},
        "fit": {...},
        "risk": {...}
      },
      "sales_context": {
        "who_they_are": "Onur İnşaat PM...",
        "recommended_opening": "...",
        "key_questions_for_call": ["...", "...", "..."]
      }
    },
    "osint": {"provider": "stub", ...}
  }
}
```

### 4.3. DB + Redis verify

```bash
# DB — score updated
docker exec lisent-qualifier-db-tzobyr psql -U app -d lead_qualifier -c \
  "SELECT id, score, score_breakdown->'pre_score_ensemble'->>'median_direct_score' AS median
   FROM qualifier_leads WHERE id='$LEAD_ID'"

# Redis — queue should be empty (consumed)
docker exec lisent-qualifier-redis-h1lcug redis-cli LLEN "prescore:queue:<TENANT_ID>"
# → 0

# OSINT profile DB — stub provider saved
docker exec lisent-qualifier-db-tzobyr psql -U app -d lead_qualifier -c \
  "SELECT provider, refetch_count FROM qualifier_osint_profiles WHERE email='ayse@onurinsaat.com.tr'"
# → provider=stub, refetch_count=0 (or 1+ if same email seen before)
```

### 4.4. Metrics

Qualifier'ın `/metrics` endpoint'ini scrape ederek:
```bash
curl -sS "https://qualifier.lisent.ai/metrics" | grep -E "prescore_|osint_"
```
Sağlıklı sinyal: `prescore_latency_seconds` bucket'larında değerler var, `prescore_fallback_total` 0 veya çok düşük.

### 4.5. Shadow observation (Stage 1 — 24h)

- `prescore_fallback_total` rate < %5
- `prescore_latency_seconds{quantile="0.95"}` < 10s
- `prescore_divergence_total` rate < %10 (LLM vs formula tutarlı)
- Grafana dashboard: yeni bir panel ekle, bu metric'leri izle
- Hataya bak: `docker logs ... | grep -iE "prescore.*fail|ensemble.*fail"`

Başarılı → Stage 2.

## 5. Shadow mode activation — Stage 2: real OSINT

Phase 0 validation'ı geçmiş phoneinfoga + holehe-api'yi aktif et:

```
Dokploy Environment:
  OSINT_ENABLED = true
```

Qualifier restart. 5-10 synthetic lead postla, yukarıdaki smoke'u tekrarla.

### 5.1. Expected delta

- `explanation.osint.provider == "self_hosted"`
- `explanation.osint.email.registered_sites` dolu (mail'in kayıtlı olduğu siteler)
- `explanation.osint.phone.country` + `line_type` dolu
- `explanation.pre_score_ensemble.signals.identity.osint_digital_footprint` = `low/medium/high`

### 5.2. Monitoring — 1 hafta

```
Grafana panel sorguları:
  osint_latency_seconds_bucket{provider="self_hosted",quantile="0.95"}  < 10
  osint_failures_total{reason="timeout"}  rate  < %10
  osint_cache_hit_total / (osint_cache_hit_total + osint_cache_miss_total)  > %30 after 48h warmup
  prescore_latency_seconds_bucket{quantile="0.95"}  < 12
```

Ban risk sinyalleri:
- Holehe modüllerinin sıra sıra 429 dönmeye başlaması → throttle artır (`OSINT_THROTTLE_INTERVAL_MS` config)
- PhoneInfoga response 400 → number format sorunu, adapter logic revisit

## 6. Rollback plan

### Level 1 — OSINT off (1 dakika)
```
Dokploy Environment: OSINT_ENABLED=false
→ qualifier restart → stub adapter → LLM judge sadece form'la çalışır
```

### Level 2 — Worker off (1 dakika)
```
Dokploy Environment: PRESCORE_WORKER_ENABLED=false
→ qualifier restart → enqueue skipped, worker idle
→ LEGACY scoring (RuleBasedScorer) kesintisiz devam
```

### Level 3 — Full revert (git revert + DB restore)
```bash
# Qualifier previous commit
git revert <phase3-commit-range>
git push origin main
# Dokploy auto-deploy önceki haline

# Redis cleanup
docker exec lisent-qualifier-redis-h1lcug redis-cli \
  --scan --pattern 'prescore:*' | xargs -I {} docker exec lisent-qualifier-redis-h1lcug redis-cli DEL {}

# qualifier_osint_profiles tablosu — BIRAKILIR (veri kaybı olmasın)
# İleride plan yeniden açılırsa aynı tablo kullanılabilir
```

DB backup restore sadece migration 004 problemi varsa; tablo additive olduğundan
normalde gerek yok.

## 7. Phase 7 (future, Shadow gözlem sonrası)

30 gün başarılı shadow operation sonrası:
1. Legacy `ProcessWebhookLeadHandler` → `enqueue_lead` call eklenir (webhook path da yeni pipeline'a)
2. `app/domain/scoring/scorer.py` + `tests/unit/domain/test_scorer.py` silinir
3. `compute_threshold` tenant.config'ten okuyan yeni fonksiyonla değiştirilir
4. CRM write-through + AI metadata update + fast/chat routing PreScoreService'e migrate edilir
5. Final integration test: `/webhook/lead/{token}` → enqueue → worker → CRM update

Bu aşamada CRM/WhatsApp greeting worker entegrasyonu riskli — 2-3 gün
dedicated implementation + 1 hafta shadow + release. **Phase 7 yeni bir plan
ile başlar**, bu plan dışı.

## 8. Known pre-existing issue

`tests/integration/test_phase2d_score_stream.py::TestLiveStream` + `TestReplay`
— Phase 2.M merkezli commit'te de hang ediyor (Phase 3 değişiklikleriyle
ilgisiz). SSE stream ASGI transport altında live event gelişini beklerken
takılıyor. `integration test` olarak skip edilebilir; manuel E2E smoke (user
memory 2026-04-23'te kaydettiği) hâlâ yeşil.

## 9. Acceptance criteria

- [ ] Phase 1.1-1.5 → 1: `lisent-osint-stack` deployed + healthy
- [ ] Phase 2: migration 004 applied + RLS verified
- [ ] Phase 3: qualifier deployed with new env vars, flag OFF
- [ ] Phase 4: Stage 1 shadow — stub OSINT + LLM judge OK
- [ ] Phase 5: Stage 2 shadow — real OSINT + LLM judge OK
- [ ] 7 gün shadow observation (fallback <%5, divergence <%10)
- [ ] **Flag flip NOT in this phase** — Phase 7 plan aşamasında karar verilir
