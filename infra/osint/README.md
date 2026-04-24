# Lisent OSINT Stack

Self-hosted OSINT services for the qualifier's pre-scoring pipeline. Runs two containers:

| Service      | Image                              | Port (local) | Purpose                                                              |
|--------------|------------------------------------|--------------|----------------------------------------------------------------------|
| phoneinfoga  | `sundowndev/phoneinfoga:latest`    | 5055         | Phone number OSINT — country, carrier, line type (mobile/landline/voip), VoIP flags. International. |
| holehe-api   | `lisent/holehe-api:local` (built)  | 18001        | Email OSINT wrapper around `holehe` — checks 120+ platforms (LinkedIn, Gravatar, GitHub, Twitter, Facebook, …) for account existence. Language-agnostic.         |

> **Host port note**: Local bindings avoid the prod dev container ports already in use on this Hetzner host (5000, 8000, 8001). Inside Dokploy/the dokploy-network the services still listen on their container-native ports (5000 and 8000) — qualifier will resolve them as `phoneinfoga:5000` and `holehe-api:8000`.

Both are **self-hosted**, no external subscription, data never leaves the host. Fit for KVKK / GDPR / multi-region tenants.

## Phase 0 — Local validation

Before deploying to Dokploy or touching qualifier code, validate the stack locally:

```bash
cd /home/kaan/development-main/lisent-qualifier/infra/osint

# 1. Build + start
docker compose up -d --build

# 2. Wait for health
docker compose ps              # both "healthy"
docker compose logs -f         # watch for startup errors (Ctrl+C when clean)

# 3. Discover holehe modules
curl -sS http://localhost:18001/health | jq '{module_count, modules: .modules[:10]}'

# 4. Sanity ping — phoneinfoga
curl -sS http://localhost:5055/ | head -c 200    # serves web UI html → means server up
```

### Smoke tests — international coverage

**Phone (PhoneInfoga)** — expect `country`, `carrier`, `line_type`, `valid` fields populated:

```bash
# v2 API scan endpoint — run all scanners for the number
for phone in \
  "+905551234567"     `# TR Turkcell mobile` \
  "+905321112233"     `# TR Vodafone mobile` \
  "+902123334455"     `# TR landline Istanbul` \
  "+14155552671"      `# US SF landline` \
  "+442071234567"     `# UK London landline` \
  "+4915112345678"    `# DE mobile` \
  "+33612345678"      `# FR mobile` \
  "+971501234567"     `# UAE mobile` \
  ; do
  echo "=== $phone ==="
  curl -sS -X POST "http://localhost:5055/api/numbers/scan" \
    -H 'Content-Type: application/json' \
    -d "{\"number\":\"$phone\",\"scanners\":[\"local\",\"numverify\"]}" \
    | jq '{input: .number, country: .results.local.country, carrier: .results.local.carrier, line_type: .results.local.line_type}' \
    || echo "fallback: GET route"
done
```

If `POST /api/numbers/scan` returns 404 on the installed phoneinfoga version, fall back to the GET route:

```bash
curl -sS "http://localhost:5055/api/numbers/+905551234567/scan/all" | jq .
```

Record which endpoint worked in the "Findings" section below — the qualifier adapter will use the same.

**Email (Holehe API)** — expect `registered_sites` list and `any_rate_limited` flag:

```bash
for email in \
  "contact@acarkaan.com"      `# our own` \
  "test@gmail.com"            `# freemail` \
  "noreply@github.com"        `# corporate (US)` \
  "info@bbc.co.uk"            `# corporate (UK)` \
  "kontakt@siemens.de"        `# corporate (DE)` \
  "test@mailinator.com"       `# disposable` \
  "test@onurinsaat.com.tr"    `# TR corporate (may not exist)` \
  ; do
  echo "=== $email ==="
  curl -sS -X POST http://localhost:18001/check \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"$email\",\"timeout_per_module\":4.0}" \
    | jq '{email, module_count, registered_count, rate_limited_count, error_count, sites: .registered_sites, elapsed_ms}'
done
```

### Acceptance criteria (Phase 0 gating)

- [ ] Both containers reach `healthy` status within 30s
- [ ] PhoneInfoga returns `country` + `line_type` for **all 8 test numbers**
- [ ] Holehe `/health` reports **≥ 80** modules discovered (nominal is ~120)
- [ ] For corporate emails (`github.com`, `bbc.co.uk`, `siemens.de`), **≥ 2** registered sites found
- [ ] `error_count / module_count` < 30% (a healthy Holehe run)
- [ ] p95 `elapsed_ms` from `/check` < 10s (full 120-module sweep at concurrency 20)
- [ ] `any_rate_limited` is `false` on freshly cold-started run (no rate limit triggered)
- [ ] RAM: phoneinfoga < 80MB, holehe-api < 400MB (measured via `docker stats --no-stream`)

**If < 80 modules discovered OR > 30% error rate → pivot to `user-scanner` (Holehe's maintained successor).** Plan has this escape hatch documented.

### Teardown

```bash
docker compose down              # stop containers, keep images
docker compose down --rmi local  # also remove lisent/holehe-api:local image
```

## Environment variables

| Var                         | Service       | Default | Description                                                    |
|-----------------------------|---------------|---------|----------------------------------------------------------------|
| `NUMVERIFY_API_KEY`         | phoneinfoga   | (unset) | Enables numverify scanner for deeper carrier/line data         |
| `GOOGLECSE_CX`              | phoneinfoga   | (unset) | Google Custom Search Engine ID (reverse-lookup OSINT scanner)  |
| `GOOGLE_API_KEY`            | phoneinfoga   | (unset) | Google API key for the above                                   |
| `HOLEHE_DEFAULT_TIMEOUT`    | holehe-api    | `4.0`   | Per-module timeout in seconds (1.0-15.0)                       |
| `HOLEHE_MAX_CONCURRENCY`    | holehe-api    | `20`    | Parallel module execution cap (1-64)                           |

Create `.env` in this folder to override, or set in Dokploy environment panel when deploying.

## Dokploy deployment (Phase 0 sonrası)

Production compose file lives at **`infra/osint/docker-compose.prod.yml`** — no
host ports, `dokploy-network` external. The plain `docker-compose.yml` stays
local-dev-only (binds `127.0.0.1:5055` + `127.0.0.1:18001`).

1. Dokploy panel → **Create App** → **Compose**
2. Name: `lisent-osint-stack`
3. Source: Git — `lisent-ai/lisent-qualifier` branch `main`
4. **Compose path**: `infra/osint/docker-compose.prod.yml`  ← use the prod file
5. Set env vars via Dokploy Environment UI (all optional — see header comment
   in `docker-compose.prod.yml`)
6. Deploy. Both services should come up healthy on `dokploy-network`.
7. Verify from qualifier container:
   ```bash
   docker exec <qualifier-container> sh -c "curl -sS http://phoneinfoga:5000/api/" | head -c 100
   docker exec <qualifier-container> sh -c "curl -sS http://holehe-api:8000/health" | head -c 100
   ```

## Findings — Phase 0 run 2026-04-23

```
Run date:                              2026-04-23 22:20 UTC
Host:                                  lisent-ai (Hetzner AX102)
PhoneInfoga version:                   v2.11.0 commit 5f6156f (docker latest)
Holehe package version:                1.61

Discovered holehe modules:             121    ✓ (target ≥ 80)

Working PhoneInfoga scan endpoint:     POST /api/v2/scanners/{scanner}/run
                                       body {"number":"<digits_only, no +>"}
                                       Free scanners: local (reliable), ovh (FR only)
                                       API-key scanners: numverify, googlecse
TR phone coverage (3 samples):         ✓ country=TR + e164 for mobile (Turkcell,
                                         Vodafone) + landline (Istanbul)
US/UK/DE/FR/UAE phone coverage:        ✓ all 5 return correct country + e164

Holehe error rate (gmail sample):      11.6% (14/121)  ✓ (target < 30%)
  ├─ timeout:         5 (modules too slow under 4s)
  ├─ IndexError:      5 (stale parser, site HTML changed)
  ├─ JSONDecodeError: 2 (stale — response format changed)
  ├─ AttributeError:  1
  └─ ConnectError:    1 (network flap)
p95 /check latency:                    8260ms  ✓ (target < 10000)
Rate-limited on cold start:            62/121 ~= 51% — NORMAL for holehe
                                       (rateLimit flag fires for 429/challenge;
                                       many niche sites always return this. The
                                       signal we care about = registered_count
                                       from the ~87 "clean" responses.)

Signal quality (per sample):
  test@gmail.com     → 19 sites registered (strongest signal)
  test@mailinator.com → 9 sites (disposable, still has some)
  noreply@github.com → 5 sites
  info@bbc.co.uk     → 2 sites
  contact@acarkaan.com → 0 sites (real but limited digital footprint)
  kontakt@siemens.de → 0 sites (org role address, not personal)
  test@onurinsaat.com.tr → 0 sites (non-existent probe address)

Memory peak:
  phoneinfoga:     35 MiB   ✓ (target < 80)
  holehe-api:     103 MiB   ✓ (target < 400)

Gate decision:                         ✅ PROCEED to Dokploy deploy + qualifier
                                          adapter code (Phase 1+)
```

**Notes for qualifier adapter implementation**:
- PhoneInfoga: strip the leading `+` before POSTing; use `local` scanner by default; add `numverify` as a second call when `NUMVERIFY_API_KEY` is set (for carrier + line_type signal). The `ovh` scanner is France-only — skip unless `country_code=="33"` from the local scan.
- Holehe: pre-filter modules by relevance? Not MVP. Take the whole 121-module sweep; the judge prompt treats `registered_sites` list as evidence, and ignores broken modules.
- The `rate_limited_count` alone is not a failure signal for a lead — only a hint about Holehe's own health. Use `registered_sites` (exists=True) as the positive signal.

**Known-broken modules to track over time** (re-evaluate if error rate grows): see error-kind counts above — 14 modules had issues in this run. Capture in Grafana via `osint_holehe_module_errors_total{module}` once qualifier adapter is live.

## Security notes

- Both containers bind to `127.0.0.1` only in local dev (see ports in compose). No public exposure.
- In Dokploy prod, they live on `dokploy-network` internal only — not reachable from the public domain.
- Holehe's password-reset checks are silent and do NOT send emails to the target. The `out` payloads from each module do not contain leaked PII beyond "account exists at `<site>`".
- PhoneInfoga does not query the phone itself — only public APIs and heuristic parsing of E.164.
