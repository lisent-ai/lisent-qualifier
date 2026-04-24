#!/usr/bin/env bash
# Phase 3 pre-scoring pipeline — production smoke test.
#
# Requires (env vars):
#   QUALIFIER_BASE_URL   — https://qualifier.lisent.ai (default) or http://localhost:8000
#   TEST_TENANT_API_KEY  — a dev/test tenant API key (sk_live_...)
#
# Optional:
#   EXTERNAL_REF_PREFIX  — "smoke" by default; suffixed with timestamp
#   WAIT_SECONDS         — how long to wait after POST for the worker (default 8)
#
# Exit codes:
#   0 — all checks passed
#   1 — setup error (missing env, unreachable)
#   2 — smoke assertion failed (details in output)

set -eu

BASE_URL="${QUALIFIER_BASE_URL:-https://qualifier.lisent.ai}"
API_KEY="${TEST_TENANT_API_KEY:-}"
PREFIX="${EXTERNAL_REF_PREFIX:-smoke}"
WAIT="${WAIT_SECONDS:-8}"

if [[ -z "$API_KEY" ]]; then
  echo "ERROR: TEST_TENANT_API_KEY env var required" >&2
  exit 1
fi

command -v curl >/dev/null || { echo "ERROR: curl not found" >&2; exit 1; }
command -v jq   >/dev/null || { echo "ERROR: jq not found" >&2; exit 1; }

EXTREF="${PREFIX}-$(date +%s)"

echo "=== Phase 3 pre-scoring smoke ==="
echo "Base URL:     $BASE_URL"
echo "External ref: $EXTREF"
echo "Wait:         ${WAIT}s"
echo

# ─── 1. POST /v1/leads — kurumsal inşaat lead örneği ─────────────────────

echo "[1/4] POST /v1/leads"
CREATE_JSON=$(curl -sS -X POST "$BASE_URL/v1/leads" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{
    \"external_ref\": \"$EXTREF\",
    \"name\": \"Ayşe Yılmaz\",
    \"email\": \"ayse@onurinsaat.com.tr\",
    \"phone\": \"+905321112233\",
    \"city\": \"Ankara\",
    \"source\": \"phase3_smoke\",
    \"project_type\": \"commercial\",
    \"budget_range\": \"10m_plus\",
    \"notes\": \"Ankara Çankaya 15M TL plaza projesi, Eylülde başlamak istiyoruz, karar verici benim.\"
  }")

LEAD_ID=$(echo "$CREATE_JSON" | jq -r '.id // empty')
if [[ -z "$LEAD_ID" ]]; then
  echo "FAIL: POST /v1/leads did not return an id" >&2
  echo "$CREATE_JSON" | jq . >&2
  exit 2
fi
INITIAL_SCORE=$(echo "$CREATE_JSON" | jq -r '.score // 0')
echo "  lead_id=$LEAD_ID  initial_score=$INITIAL_SCORE  (expected 0, worker will update)"

# ─── 2. Wait for worker ──────────────────────────────────────────────────

echo "[2/4] Waiting ${WAIT}s for PreScoreWorker to process…"
sleep "$WAIT"

# ─── 3. GET /v1/leads/{id}/score ─────────────────────────────────────────

echo "[3/4] GET /v1/leads/$LEAD_ID/score"
SCORE_JSON=$(curl -sS "$BASE_URL/v1/leads/$LEAD_ID/score" \
  -H "Authorization: Bearer $API_KEY")

FINAL_SCORE=$(echo "$SCORE_JSON" | jq -r '.score // empty')
THRESHOLD=$(echo "$SCORE_JSON" | jq -r '.threshold // empty')
HAS_ENSEMBLE=$(echo "$SCORE_JSON" | jq -r 'has("explanation") and (.explanation | has("pre_score_ensemble"))')
HAS_OSINT=$(echo "$SCORE_JSON" | jq -r 'has("explanation") and (.explanation | has("osint"))')

echo "  final_score=$FINAL_SCORE  threshold=$THRESHOLD"
echo "  has pre_score_ensemble: $HAS_ENSEMBLE"
echo "  has osint: $HAS_OSINT"

if [[ -z "$FINAL_SCORE" || "$FINAL_SCORE" == "null" ]]; then
  echo "FAIL: score endpoint returned no score" >&2
  echo "$SCORE_JSON" | jq . >&2
  exit 2
fi

if [[ "$FINAL_SCORE" == "0" ]]; then
  echo "WARN: score still 0 — worker may not have run yet (or flag off)" >&2
  echo "  If PRESCORE_WORKER_ENABLED=false, this is expected; rerun with flag on." >&2
  # Not a hard fail — allows smoke to work in pre-activation check.
fi

# ─── 4. Glass-box inspection ────────────────────────────────────────────

if [[ "$HAS_ENSEMBLE" == "true" ]]; then
  echo "[4/4] Ensemble breakdown:"
  echo "$SCORE_JSON" | jq '{
    median: .explanation.pre_score_ensemble.median_direct_score,
    formula_audit: .explanation.pre_score_ensemble.formula_audit_score,
    divergent: .explanation.pre_score_ensemble.divergent,
    persona_scores: .explanation.pre_score_ensemble.persona_scores,
    extraction_confidence: .explanation.pre_score_ensemble.extraction_confidence,
    sales_context: .explanation.pre_score_ensemble.sales_context
  }'

  DIVERGENT=$(echo "$SCORE_JSON" | jq -r '.explanation.pre_score_ensemble.divergent // false')
  if [[ "$DIVERGENT" == "true" ]]; then
    echo "  DIVERGENCE FLAG RAISED — LLM vs formula drift > threshold"
    echo "  Not a failure but worth investigating if persistent"
  fi
fi

if [[ "$HAS_OSINT" == "true" ]]; then
  echo "OSINT provenance:"
  echo "$SCORE_JSON" | jq '{
    provider: .explanation.osint.provider,
    phone_country: .explanation.osint.phone.country,
    email_domain_type: .explanation.osint.email.domain_type,
    site_count: .explanation.osint.email.site_count,
    cache_hit: .explanation.osint.cache_hit
  }'
fi

echo
echo "=== SMOKE PASSED ==="
echo "lead_id: $LEAD_ID"
echo "final_score: $FINAL_SCORE  (threshold=$THRESHOLD)"
exit 0
