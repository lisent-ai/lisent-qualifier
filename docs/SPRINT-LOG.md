# AI Lead Qualifier — Sprint Log

---

## Sprint 1: Backend API Foundation
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**DB Schema Degisiklikleri:**
- `qualifier_leads` tablosuna `score_breakdown JSONB` ve `extra_data JSONB` kolonlari eklendi
- `scripts/db_init.sql` guncellendi

**Backend API (`app/api/leads/router.py`):**
- `GET /internal/leads/{company_id}` — Tam filtreleme destegi eklendi:
  - Query params: `status`, `score_min`, `score_max`, `source`, `path`, `search` (ILIKE), `sort_by`, `sort_dir`, `date_from`, `date_to`
  - Response'a `total` count eklendi (pagination icin)
  - Default limit 100 → 20 olarak dusuruldu
- `GET /internal/leads/{company_id}/active-sessions` — Yeni endpoint: aktif chat session'lari listeler
- `GET /internal/leads/{company_id}/{lead_id}` — `score_breakdown` alani eklendi

**DB Repository (`app/infrastructure/db/lead_repo.py`):**
- `upsert_lead()` — `score_breakdown` parametresi eklendi
- `list_leads()` — Dinamik WHERE clause ile filtreleme, COUNT(*) ile total
- `get_lead_with_session()` — `score_breakdown` JOIN'e eklendi
- `list_active_sessions()` — Yeni fonksiyon

**Scoring Integration:**
- `app/application/lead_intake/handler.py` — `_fast_path()` ve `_chat_path()` return dict'lerine `score_breakdown` eklendi
- `app/api/webhook/router.py` — `upsert_lead()` cagrisina `score_breakdown` parametre olarak eklendi

**BFF Proxy:**
- `src/app/api/qualifier/[...path]/route.ts` — `POST` export eklendi

### Degisiklik Yapilan Dosyalar
- `ai-lead-qualifier/scripts/db_init.sql`
- `ai-lead-qualifier/app/infrastructure/db/lead_repo.py`
- `ai-lead-qualifier/app/api/leads/router.py`
- `ai-lead-qualifier/app/application/lead_intake/handler.py`
- `ai-lead-qualifier/app/api/webhook/router.py`
- `lisent.ai-crm-web/src/app/api/qualifier/[...path]/route.ts`

---

## Sprint 2: Frontend Dashboard Iskeleti + Lead Tablosu
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**Qualifier Client (`src/lib/qualifier/client.ts`):**
- Tamamen yeniden yazildi
- Yeni tipler: `ScoreBreakdown`, `LeadFilterParams`, `PaginatedLeads`, `ActiveSession`
- `listQualifierLeads(companyId, params?)` — filtre parametreleri + paginated response
- `getQualifierLeadDetail(companyId, leadId)` — lead detay
- `getActiveQualifierSessions(companyId)` — aktif oturumlar

**Yeni Component Dizini: `src/components/dashboard/companies/qualifier/`**
- `qualifier-dashboard.tsx` — 3 tab'li ana orkestrator (Leadler, Aktif Oturumlar, Ayarlar)
  - `useTransition` ile tab gecisleri
  - Lead detay goruntuleme (inline, Sprint 3'te ayri component'e tasindi)
  - Aktif oturumlar paneli (15sn polling, canli yesil nabiz animasyonu)
- `qualifier-lead-table.tsx` — Siralanabilir, sayfalanabilir tablo
  - Kolon: Ad, Telefon, Skor, Kaynak, Proje, Yol, Durum, Tarih
  - Skor renk kodlu pill (emerald/amber/slate)
  - Pagination kontrolları (Onceki/Sonraki + sayfa boyutu secici)
- `qualifier-lead-filters.tsx` — Filtre cubugu
  - Arama (300ms debounce), durum, kaynak, yol dropdown'ları
  - Skor aralığı (min-max), "Temizle" butonu
- `qualifier-skeleton.tsx` — Tablo + session skeleton (animate-pulse)
- `qualifier-empty-state.tsx` — Zengin bos durum componenti (SVG icon + aciklama + aksiyon butonu)

**Entegrasyon (`company-selected-panel.tsx`):**
- `CompanyQualifierLeads` → `QualifierDashboard` ile degistirildi
- `CompanyQualifierPanel` artik dashboard'un Ayarlar tab'inda
- Alt kisimdaki ayri qualifier panel kaldirildi

**Eski Component Uyumlulugu:**
- `company-qualifier-leads.tsx` — `listQualifierLeads` return tipi degisikligine uyum saglandi

### Degisiklik Yapilan Dosyalar
- `lisent.ai-crm-web/src/lib/qualifier/client.ts` (yeniden yazildi)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-dashboard.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-lead-table.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-lead-filters.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-skeleton.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-empty-state.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/company-selected-panel.tsx` (guncellendi)
- `lisent.ai-crm-web/src/components/dashboard/companies/company-qualifier-leads.tsx` (uyumluluk fix)

---

## Sprint 3: Lead Detay Zenginlestirme + Aktif Oturumlar + Animasyonlar
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**Yeni Component'ler:**
- `qualifier-score-breakdown.tsx` — 5 boyutlu skor kirilimi
  - Butce (30), Zamanlama (25), Proje Tipi (20), Yetki (15), Veri Kalitesi (10)
  - Renkli progress bar'lar (violet/cyan/amber/emerald/slate)
  - Animasyonlu genisleme (transition-all duration-700 ease-out)
- `qualifier-bant-radar.tsx` — SVG radar/pentagon chart
  - 4 eksen: Budget, Authority, Need, Timeline (0-25 scale)
  - Dolu alan + veri noktalari + grid cizgileri
  - Sag tarafta skor detaylari + BANT notlari
- `qualifier-conversation.tsx` — Gelistirilmis konusma goruntuleyici
  - Tarih ayiraclari ile gun bazli gruplama
  - Auto-scroll to bottom (useRef + useEffect)
  - User (violet) / AI (beyaz border'li) mesaj ayrimi
  - Zaman damgalari
- `qualifier-reasoning.tsx` — Yapilandirilmis AI degerlendirme goruntuleme
  - Ozet, skor aciklamasi, onemli sinyaller (bullet list), onerilen yaklasim, olasi itirazlar
  - Oncelik badge'i (yuksek/orta/dusuk — emerald/amber/slate)
  - Fallback: yapilandirilmamis JSON icin raw goruntuleme
- `qualifier-lead-detail.tsx` — Ana lead detay component'i
  - Tum alt component'leri birlestiren zengin gorunum
  - Header: isim, telefon, skor badge, CRM gonderim durumu
  - Info grid (kaynak, proje tipi, butce, sehir, email, yol, durum, tarih, CRM gonderim zamani)
  - Yan yana layout: skor kirilimi + BANT radar (lg:grid-cols-2)
  - AI degerlendirme + konusma gecmisi

**Dashboard Guncellemeleri:**
- `qualifier-dashboard.tsx` — Inline `LeadDetailInline` kaldirildi, `QualifierLeadDetailView` ile degistirildi
- Aktif oturum kartlarina tiklama → lead detaya gecis eklendi
- Canli yesil nabiz animasyonu (ping + pulse)
- Hover efektleri (-translate-y-0.5 + shadow-md)

### Degisiklik Yapilan Dosyalar
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-score-breakdown.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-bant-radar.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-conversation.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-reasoning.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-lead-detail.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-dashboard.tsx` (guncellendi)

---

## Sprint A Backend: Activity Log + Status Lifecycle + Notes + Assignment + Handoffs
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**DB Tablolari (PostgreSQL):**
- `activity_log` — Tum lead olaylarinin kaydi (status_change, note_added, score_update, assignment, handoff, message)
  - Indexler: lead_db_id + created_at DESC, company_id + created_at DESC
- `lead_notes` — Internal notlar (author, content, pinned, created_at)
  - Index: lead_db_id + created_at DESC
- `handoff_attempts` — CRM handoff retry kayitlari (status, error_message, attempt_count)
- `qualifier_leads.assigned_to TEXT` — Yeni kolon

**Status Lifecycle Validasyonu:**
- `lead_repo.py` icinde `VALID_TRANSITIONS` dict'i:
  - `new → contacted, qualifying, qualified, lost, archived`
  - `contacted → qualifying, qualified, lost, archived`
  - `qualifying → qualified, lost, archived`
  - `qualified → negotiation, won, lost, archived`
  - `negotiation → won, lost, archived`
  - `lost → new, archived` (re-qualification)
  - `archived → new`
- Gecersiz gecislerde `ValueError` firlatilir

**Yeni API Endpoint'leri (`app/api/leads/router.py`):**
- `PATCH /internal/leads/{cid}/{lid}/status` — Durum degistirme (validasyon + activity log)
- `GET /internal/leads/{cid}/{lid}/activity` — Activity timeline (filtreleme + pagination)
- `POST /internal/leads/{cid}/{lid}/notes` — Not ekleme (activity log'a da yazar)
- `GET /internal/leads/{cid}/{lid}/notes` — Notlari listele (pinned oncelikli)
- `DELETE /internal/leads/{cid}/{lid}/notes/{nid}` — Not silme
- `PATCH /internal/leads/{cid}/{lid}/notes/{nid}` — Pin toggle
- `PATCH /internal/leads/{cid}/{lid}/assign` — Lead atama (activity log'a yazar)
- `GET /internal/handoffs/{cid}` — CRM handoff listesi

**DB Repository Fonksiyonlari (`lead_repo.py`):**
- `insert_activity()` — Activity log'a kayit
- `list_activities()` — Filtreleme + pagination
- `update_lead_status_with_log()` — Atomic status degisim + activity log (transaction)
- `create_note()` — Not + activity log (transaction)
- `list_notes()`, `delete_note()`, `toggle_note_pin()`
- `assign_lead()` — Atomic atama + activity log (transaction)
- `list_handoffs()` — CRM handoff listesi

### Test Sonuclari
- Status change: `new → contacted` basarili, activity_id dondu
- Note create: Not olusturuldu, activity_log'a `note_added` kaydedildi
- Activity timeline: 2 event (status_change + note_added) kronolojik dondu
- Assignment: Lead atama basarili
- Handoffs: Liste calisiyor (bos — henuz handoff yok)

### Degisiklik Yapilan Dosyalar
- `ai-lead-qualifier/scripts/db_init.sql` (yeni tablolar eklendi)
- `ai-lead-qualifier/app/infrastructure/db/lead_repo.py` (~200 satir yeni fonksiyon)
- `ai-lead-qualifier/app/api/leads/router.py` (tamamen yeniden yazildi — 8 yeni endpoint)

---

## Onceki Degisiklikler (Sprint Oncesi)

### Webhook URL Fix
- `company-qualifier-panel.tsx` — Frontend'in kendi URL'ini olusturmasi kaldirildi, backend'den gelen `webhookUrl` kullaniliyor
- `lisent.ai-CRM-service/.env` — `AI_QUALIFIER_BASE_URL=http://localhost:8000` eklendi

### Env Fixes
- `lisent.ai-crm-web/.env.local` — `QUALIFIER_BASE_URL=http://localhost:8000` eklendi
- `ai-lead-qualifier/.env` — `CRM_BASE_URL` Docker container hostname'e guncellendi (`http://lisentai-crm-service-app-1:9090`)

### CLAUDE.md Dosyalari
- `ai-lead-qualifier/CLAUDE.md` — Proje dokumantasyonu olusturuldu
- `lisent.ai-CRM-service/CLAUDE.md` — Proje dokumantasyonu olusturuldu
- `lisent.ai-crm-web/CLAUDE.md` — Proje dokumantasyonu olusturuldu

---

## Sprint A Frontend: Timeline, Notes, Status Buttons, Assignment UI
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**Qualifier Client Extension (`src/lib/qualifier/client.ts`):**
- Yeni tipler: `ActivityEvent`, `LeadNote`, `HandoffRecord`
- `requestQualifier()` — `RequestInit` destegi eklendi (POST/PATCH/DELETE icin)
- Yeni fonksiyonlar:
  - `updateLeadStatus()` — PATCH status + reason
  - `getLeadActivity()` — Activity timeline
  - `getLeadNotes()`, `addLeadNote()`, `deleteLeadNote()`, `toggleNotePin()` — Notes CRUD
  - `assignLead()` — Lead atama
  - `getHandoffs()` — CRM handoff listesi

**Yeni Component'ler:**
- `qualifier-activity-timeline.tsx` — Dikey timeline component'i
  - Event tiplerine gore renk kodlu icon'lar (status_change: violet, note: blue, score: amber, assignment: cyan, handoff: emerald)
  - Her event icin: actor, zaman damgasi, payload goruntuleme
  - Status change: eski → yeni durum + neden
  - Note: icerik preview
  - Assignment: eski → yeni atama
  - Score update: eski → yeni skor
  - Skeleton loading state
  - Max 80 satir yukseklik, scroll

- `qualifier-notes.tsx` — Internal notlar paneli
  - Inline compose box (textarea + "Ekle" butonu)
  - Not kartlari: author, tarih, icerik, pin durumu
  - Pin toggle + silme butonlari
  - Pinned notlar ust sirada
  - Skeleton loading + bos durum
  - Max 72 satir yukseklik, scroll

- `qualifier-status-actions.tsx` — Durum degistirme butonlari
  - Mevcut statuse gore izin verilen gecisleri gosterir
  - Her gecis icin renkli buton (blue/amber/emerald/rose/violet)
  - Confirmation modal: hedef durum gosterimi + neden textarea + Onayla/Iptal
  - Hata gosterimi
  - Turkce durum etiketleri

**Lead Detail Guncellemesi (`qualifier-lead-detail.tsx`):**
- `companyId` prop'u eklendi (status/notes/activity icin gerekli)
- 3 tab'li gorunum: Detay / Notlar / Aktivite
- Header'a durum badge'i eklendi (renk kodlu)
- `QualifierStatusActions` entegre edildi (header altinda)
- Notlar tab'inda `QualifierNotes` component'i
- Aktivite tab'inda `QualifierActivityTimeline` component'i
- Status degisiminde local state guncellenmesi (optimistic-like)

**Dashboard Guncellemesi (`qualifier-dashboard.tsx`):**
- `QualifierLeadDetailView`'e `companyId` prop'u eklendi

### Degisiklik Yapilan Dosyalar
- `lisent.ai-crm-web/src/lib/qualifier/client.ts` (genisletildi — yeni tipler + 7 yeni fonksiyon)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-activity-timeline.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-notes.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-status-actions.tsx` (yeni)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-lead-detail.tsx` (yeniden yazildi — tab yapisi + yeni component entegrasyonu)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-dashboard.tsx` (companyId prop eklendi)

---

## Sprint B Backend: Tags, Bulk Ops, CSV Export, WhatsApp Templates
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**DB Tablolari:**
- `tags` — Sirket bazli etiketler (name, color, unique per company)
- `lead_tags` — Lead-tag iliskisi (many-to-many)
- `whatsapp_templates` — Mesaj sablonlari (content, variables auto-detected)
- `whatsapp_batch_sends` — Toplu gonderim batch'leri (total, sent, failed)
- `whatsapp_batch_items` — Her alici icin gonderim durumu

**Tag Sistemi (lead_repo.py + leads/router.py):**
- `POST /internal/tags/{cid}` — Tag olustur
- `GET /internal/tags/{cid}` — Tagleri listele (lead_count dahil)
- `DELETE /internal/tags/{cid}/{tid}` — Tag sil
- `POST /internal/leads/{cid}/{lid}/tags` — Lead'e tag ekle
- `DELETE /internal/leads/{cid}/{lid}/tags/{tid}` — Lead'den tag kaldir
- `GET /internal/leads/{cid}/{lid}/tags` — Lead'in taglerini getir

**Bulk Operations:**
- `POST /internal/leads/{cid}/bulk` — Toplu islem (status_change, assign, add_tag, remove_tag)
- Max 200 lead per istek
- Her lead icin activity_log kaydi (bulk: true)

**CSV Export:**
- `GET /internal/leads/{cid}/export` — StreamingResponse CSV
- Mevcut filtreleri destekler (status, score_min/max, source, path)
- Kolonlar: id, lead_id, name, phone, score, path, status, assigned_to, source, project_type, budget_range, city, email, created_at
- Route ordering fix: export ve bulk endpoint'leri {lead_id} wildcard'indan ONCE tanimlanmali

**WhatsApp Templates + Bulk Messaging (yeni templates_router.py):**
- `POST /internal/whatsapp-templates/{cid}` — Sablon olustur (variable auto-detect: {name}, {city} vb.)
- `GET /internal/whatsapp-templates/{cid}` — Sablonlari listele
- `DELETE /internal/whatsapp-templates/{cid}/{tid}` — Sablon sil
- `POST /internal/whatsapp-templates/{cid}/{tid}/preview` — Lead'ler icin resolved mesaj preview
- `POST /internal/whatsapp/bulk-send/{cid}` — Toplu gonderim baslat (background task)
- `GET /internal/whatsapp/batch/{cid}/{bid}` — Batch durumu
- Variable substitution: {name}, {phone}, {city}, {email}, {project_type}, {budget_range}, {source}
- Rate limiting: 50ms/mesaj (GreenAPI limit uyumlu)
- GreenAPI credentials: company_id → CRM lookup → id_instance + api_token

**CRM REST Client (rest_client.py):**
- `lookup_greenapi_by_company()` — company_id ile GreenAPI credentials cekme (CRM endpoint chain: company → id_instance → internal lookup)

### Test Sonuclari
- Tags: Create/list/delete + lead tagging calisiyor
- Bulk: 3 lead'in status'u ayni anda degistirildi
- CSV: Dogru formatli CSV ciktisi, tum kolonlar
- WhatsApp: Template + variable auto-detect calisiyor
- Route fix: export/bulk ile lead_id wildcard cakismasi cozuldu

### Degisiklik Yapilan Dosyalar
- `ai-lead-qualifier/scripts/db_init.sql` (5 yeni tablo)
- `ai-lead-qualifier/app/infrastructure/db/lead_repo.py` (~200 satir: tags, bulk, export, WA template fonksiyonlari)
- `ai-lead-qualifier/app/api/leads/router.py` (tags, bulk, export endpoint'leri + route ordering fix)
- `ai-lead-qualifier/app/api/whatsapp/templates_router.py` (YENI — template CRUD + bulk send + batch status)
- `ai-lead-qualifier/app/infrastructure/crm/rest_client.py` (lookup_greenapi_by_company eklendi)
- `ai-lead-qualifier/app/main.py` (wa_templates_router register)

---

## Sprint B Frontend: Tags UI, Bulk Actions, Export Button, WA Templates
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**Qualifier Client Extension (client.ts):**
- Yeni tipler: `Tag`, `WATemplate`, `WABatch`
- Tag fonksiyonlari: `getTags`, `createTag`, `deleteTag`, `addLeadTag`, `removeLeadTag`, `getLeadTags`
- Bulk: `bulkAction(companyId, leadIds, action, params)`
- Export: `getExportUrl(companyId, params)` — dogrudan download linki
- WA Template: `getWATemplates`, `createWATemplate`, `deleteWATemplate`, `previewWATemplate`, `bulkSendWA`, `getWABatchStatus`

**Lead Table Yeniden Yazildi (qualifier-lead-table.tsx):**
- Checkbox kolonu (her satir + "Tumu sec")
- Secili lead'ler icin floating bulk action bar (violet theme)
  - Toplu durum degistirme dropdown
  - Toplu etiket ekleme dropdown (company tags'den)
  - Iptal butonu
- Secili satir highlight (violet-50 bg)
- Lead ismine tiklayinca detay acilma (satir click degil, isim click)
- `companyId`, `tags`, `onRefresh` prop'lari eklendi

**Filter Bar Guncellendi (qualifier-lead-filters.tsx):**
- `companyId` prop'u eklendi
- "CSV Indir" butonu (mevcut filtreleri destekler, dogrudan download linki)
- Sag tarafa ml-auto ile hizalanmis

**Dashboard Guncellendi (qualifier-dashboard.tsx):**
- Company tags state eklendi (fetchLeads ile birlikte cekilir)
- QualifierLeadTable'a companyId, tags, onRefresh prop'lari verildi
- QualifierLeadFilters'a companyId prop'u verildi

### Degisiklik Yapilan Dosyalar
- `lisent.ai-crm-web/src/lib/qualifier/client.ts` (genisletildi — tags, bulk, export, WA template fonksiyonlari)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-lead-table.tsx` (yeniden yazildi — checkbox, bulk actions)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-lead-filters.tsx` (export butonu, companyId prop)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-dashboard.tsx` (tags state, yeni props)

---

## Sprint C Backend: Score Simulation, Priority Queue, AI Summary, Follow-Up, Compare, Clone
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**DB Degisiklikleri:**
- `qualifier_leads` tablosuna `summary_json JSONB` ve `summary_generated_at TIMESTAMPTZ` kolonlari eklendi

**Yeni Domain Logic:**
- `app/domain/scoring/priority.py` — Composite urgency scorer (pure function)
  - 5 boyut: qualification_score (30%), bant_completeness (15%), recency_decay (25%), session_engagement (15%), stale_risk (15%)
  - Turkce attention reason + suggested action (call_now, takeover, follow_up, re_engage)
  - Exponential decay half-life ~8 saat

**Yeni Prompt'lar (`app/domain/conversation/prompts.py`):**
- `build_lead_summary_prompt()` — 3 cumlelik AI ozet (kim/kalifikasyon/sonraki adim)
- `build_followup_suggestion_prompt()` — BANT bosluklarina yonelik WhatsApp tarzinda tek soru

**Yeni API Endpoint'leri (leads/router.py):**
- `POST /internal/leads/{cid}/{lid}/simulate-score` — Pure function skor simulasyonu
  - Override'lar uygula → mevcut vs simule kirilim + delta + would_qualify
  - `RuleBasedScorer.breakdown()` reuse, DB yazma YOK
- `GET /internal/leads/{cid}/priority-queue?limit=20` — Urgency-sirali lead listesi
  - `fetch_priority_candidates()` → `compute_urgency()` per lead → sort by urgency desc
- `POST /internal/leads/{cid}/{lid}/ai-summary` — LLM ile 3 cumle ozet
  - Cache: `summary_json` + `summary_generated_at` (refresh=true ile bypass)
  - Graceful fallback: LLM kapaliysa temel bilgiler doner
- `GET /internal/leads/{cid}/{lid}/follow-up-suggestion` — BANT gap detection + LLM soru
  - Rule-based: eksik boyutlari tespit (budget > authority > need > timeline oncelik)
  - LLM ile dogal Turkce WhatsApp sorusu uret
  - Fallback: LLM kapaliysa hazir soru sablonlari
- `POST /internal/leads/{cid}/compare` — 2-3 lead karsilastirma (data aggregation)
- `POST /internal/leads/{cid}/{lid}/clone` — Lead klonlama (yeni lead, cloned_from referansi)

**Yeni DB Repository Fonksiyonlari (lead_repo.py):**
- `get_cached_summary()`, `save_summary()` — AI summary cache
- `clone_lead()` — Lead klonlama
- `fetch_priority_candidates()` — Priority queue veri cekme (lead + session join)
- `fetch_leads_for_comparison()` — Karsilastirma veri cekme

### Test Sonuclari
- Score Simulation: 87→97 (+10), dimension bazli delta, would_qualify=true
- Priority Queue: Urgency 50.8, "Yuksek puan ama henuz aranmamis", action: call_now
- Follow-Up: Budget eksik tespit, fallback Turkce soru calisiyor
- AI Summary: LLM kapaliyken graceful fallback calisiyor
- Lead Comparison: 2 lead basarili karsilastirma
- Lead Clone: Yeni lead olusturuldu, cloned_from referansi dogru

### Degisiklik Yapilan Dosyalar
- `ai-lead-qualifier/app/domain/scoring/priority.py` (YENI — urgency scorer)
- `ai-lead-qualifier/app/domain/conversation/prompts.py` (2 yeni prompt)
- `ai-lead-qualifier/app/infrastructure/db/lead_repo.py` (~100 satir: summary cache, clone, priority, comparison)
- `ai-lead-qualifier/app/api/leads/router.py` (6 yeni endpoint)

---

## Sprint C Frontend: Score Simulator, Priority Tab, AI Summary Card, Follow-Up Banner
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### Yapilan Isler

**Qualifier Client Extension (client.ts):**
- Yeni tipler: `SimulationResult`, `PriorityLead`, `AISummary`, `FollowUpSuggestion`
- Yeni fonksiyonlar: `simulateScore`, `getPriorityQueue`, `getAISummary`, `getFollowUpSuggestion`, `compareLeads`, `cloneLead`

**Yeni Component'ler:**
- `qualifier-priority-queue.tsx` — Urgency-sirali lead listesi
  - Gradient urgency badge (kirmizi→turuncu→gri)
  - Attention reason + suggested action butonu
  - 30sn auto-refresh
  - Lead'e tiklayinca detay acilma
- `qualifier-score-simulator.tsx` — Interaktif skor simulasyonu
  - 4 dropdown (butce, zamanlama, yetki, proje tipi)
  - "Simule Et" butonu
  - Buyuk delta gosterimi (87→97, +10)
  - Dimension bazli karsilastirmali bar chart (from→to)
  - "Bu degerlerle lead kalifiye olur!" yesil banner
- `qualifier-ai-summary.tsx` — AI ozet karti
  - Sparkle icon + violet gradient arka plan
  - 3 cumle goruntuleme (3. cumle bold)
  - "Yenile" butonu + "onbellek" badge
  - LLM kapaliyken uyari mesaji
  - Skeleton loading state
- `qualifier-followup-banner.tsx` — Takip onerisi banner'i
  - Lightbulb icon + amber gradient
  - Onerilen soru + reasoning
  - "Kopyala" butonu (clipboard)
  - Mini BANT completeness bars (B/A/N/T)
  - Completeness yuzde gosterimi

**Dashboard Guncellendi (qualifier-dashboard.tsx):**
- Yeni "Oncelik Sirasi" tab'i eklendi (4 tab: Leadler, Oncelik Sirasi, Aktif Oturumlar, Ayarlar)
- Priority tab'da lead secimi → detay gecisi

**Lead Detail Guncellendi (qualifier-lead-detail.tsx):**
- AI Summary karti en uste eklendi
- Follow-Up Banner konusmadan once eklendi
- Score Simulator skor kirilimi + BANT arasina eklendi

### Degisiklik Yapilan Dosyalar
- `lisent.ai-crm-web/src/lib/qualifier/client.ts` (6 yeni fonksiyon + 4 yeni tip)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-priority-queue.tsx` (YENI)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-score-simulator.tsx` (YENI)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-ai-summary.tsx` (YENI)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-followup-banner.tsx` (YENI)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-dashboard.tsx` (priority tab eklendi)
- `lisent.ai-crm-web/src/components/dashboard/companies/qualifier/qualifier-lead-detail.tsx` (AI summary, follow-up, simulator entegrasyonu)

---

## Sprint D: Foundation + Conversation Takeover + Tenant AI Config + Quality Score
**Tarih:** 2026-04-01
**Durum:** Tamamlandi

### D-1: Foundation — company_id Plumbing
**Tum Sprint D feature'larinin temel altyapisi.**

- `ConversationSession`'a `company_id: str` field eklendi
- `SessionStage` enum'una `HUMAN_TAKEOVER` eklendi
- Redis `session_repo` → save/get'te company_id destegi
- `ProcessWebhookLeadCommand`'a `company_id` field eklendi
- Webhook router'dan → command'a → handler'a → session'a company_id aktarimi
- WhatsApp handler'dan da ayni sekilde company_id aktarimi
- Tum yeni session'lar artik company_id bilgisini tasiyor

### D-2: Conversation Takeover (Human-in-the-Loop)
**Admin aktif AI sohbetini devralabilir, manuel mesaj gonderebilir, AI'a geri verebilir.**

**Backend (yeni `app/api/sessions/router.py`):**
- `POST /internal/sessions/{cid}/{sid}/takeover` — stage → HUMAN_TAKEOVER, onceki stage Redis'te saklanir
- `POST /internal/sessions/{cid}/{sid}/send-manual` — admin mesaji session'a kaydet + GreenAPI ile gonder
- `POST /internal/sessions/{cid}/{sid}/resume-ai` — stage → onceki stage'e geri don
- `GET /internal/sessions/{cid}/{sid}` — session bilgisi (stage, messages, score)
- WhatsApp handler'da HUMAN_TAKEOVER kontrolu: mesaj kaydet ama AI yanit URETME
- Conversation handler'da HUMAN_TAKEOVER: mesaj kabul et, should_extract_bant=false

**Frontend (`qualifier-takeover.tsx`):**
- Normal mod: "Devral" butonu (kirmizi)
- Takeover mod: Sari uyari banner, mesaj input + "Gonder" butonu, "AI'ya Geri Ver" butonu
- WhatsApp gonderim durumu geri bildirimi
- Lead detail'de konusma bolumunun ustune entegre edildi

### D-3: Tenant AI Agent Customization
**Her sirket kendi AI asistanini ozellestirebilir.**

**CRM Service (Go):**
- Yeni `internal/aiconfig/` package (model.go + handler.go)
- `company_ai_configs` tablosu (15 ozellestirilabilir alan)
- `GET /internal/company/{cid}/ai-config` — config getir (yoksa default olustur)
- `PATCH /internal/company/{cid}/ai-config` — alanlari guncelle
- Migration: `000015_company_ai_configs.up.sql`
- `gorm.io/datatypes` dependency eklendi
- AutoMigrate + main.go route register

**Qualifier Backend:**
- `fetch_company_ai_config()` — CRM'den config cekme (rest_client.py)
- `build_chat_system_prompt()` artik `company_config` parametresi aliyor
- Prompt template'e inject edilen alanlar:
  - Sirket adi + sektor odagi → persona
  - Ses tonu (professional/casual/technical/luxury)
  - Yasakli konular → ek RED LINES bolumu
  - FAQ/Bilgi bankasi → COMPANY KNOWLEDGE BASE bolumu
  - Ozel qualifying sorulari → PRIORITY QUESTIONS bolumu
- `conversation/handler.py` → stream_response() oncesi config fetch eklendi

**Frontend (`qualifier-ai-config.tsx`):**
- Tam form: sirket adi, sektor, ses tonu, dil, persona, kapanis mesaji
- Kalifikasyon esigi slider (50-100)
- Max mesaj slider (5-30)
- Calisma saatleri + fiyat ipuclari
- Yasakli konular: chip input (ekle/sil)
- FAQ: soru-cevap cift girisi (ekle/sil)
- "Ayarlari Kaydet" butonu + basarili/hata geri bildirimi
- Dashboard Ayarlar tab'inda webhook config + AI config yan yana

### D-4: Conversation Quality Score
**AI sohbet kalitesini 5 boyutta degerlendirir.**

**Backend:**
- Yeni prompt: `build_conversation_quality_prompt()` → 5 boyut (engagement, sentiment, effectiveness, density, rapport)
- `POST /internal/leads/{cid}/{lid}/conversation-quality` — LLM ile degerlendirme
- Graceful fallback: LLM kapaliyken hata mesaji

**Frontend Client:**
- `ConversationQuality` tipi + `getConversationQuality()` fonksiyonu

### Test Sonuclari
- Takeover: CHAT → HUMAN_TAKEOVER → send-manual → CHAT gecisleri basarili
- Session'da company_id doğru saklanıyor
- CRM AI Config: GET (auto-create) + PATCH calisiyor, 15 alan guncelleniyor
- Qualifier prompt'a sirket bilgileri inject ediliyor
- Conversation Quality endpoint calisiyor (LLM kapaliyken graceful fallback)

### Degisiklik Yapilan Dosyalar

**AI Lead Qualifier:**
- `app/domain/conversation/session.py` (HUMAN_TAKEOVER + company_id)
- `app/infrastructure/redis/session_repo.py` (company_id save/get)
- `app/application/lead_intake/commands.py` (company_id field)
- `app/application/lead_intake/handler.py` (company_id passthrough)
- `app/application/conversation/handler.py` (HUMAN_TAKEOVER check + company config fetch)
- `app/application/whatsapp/handler.py` (company_id + HUMAN_TAKEOVER check)
- `app/api/webhook/router.py` (company_id to command)
- `app/api/sessions/router.py` (YENI — takeover/send-manual/resume-ai/session-info)
- `app/domain/conversation/prompts.py` (config-aware prompt + summary + followup + quality prompts)
- `app/infrastructure/crm/rest_client.py` (fetch_company_ai_config)
- `app/api/leads/router.py` (conversation-quality endpoint)
- `app/main.py` (sessions_router register)

**CRM Service:**
- `internal/aiconfig/model.go` (YENI — CompanyAIConfig model)
- `internal/aiconfig/handler.go` (YENI — GET/PATCH endpoints)
- `internal/database/postgres.go` (AutoMigrate + import)
- `cmd/api/main.go` (aiconfig route register)
- `migrations/000015_company_ai_configs.up.sql` (YENI)
- `migrations/000015_company_ai_configs.down.sql` (YENI)
- `go.mod` / `go.sum` (gorm.io/datatypes eklendi)

**Frontend:**
- `src/lib/qualifier/client.ts` (takeover, quality, session fonksiyonlari)
- `src/components/dashboard/companies/qualifier/qualifier-takeover.tsx` (YENI)
- `src/components/dashboard/companies/qualifier/qualifier-ai-config.tsx` (YENI)
- `src/components/dashboard/companies/qualifier/qualifier-dashboard.tsx` (AI config entegrasyonu)
- `src/components/dashboard/companies/qualifier/qualifier-lead-detail.tsx` (takeover UI entegrasyonu)

---

## Sprint E: Frontend Polish — Animations, Glassmorphism, Transitions, Consistency
**Tarih:** 2026-04-02
**Durum:** Tamamlandi

### Yapilan Isler

**Global CSS (`globals.css`) — Tamamen Yeniden Yazildi:**
- 7 keyframe animasyon: fadeIn, slideUp, scaleIn, shimmer, slideInRight, barGrow, countUp
- Tailwind @theme inline icinde animasyon token'lari
- `.skeleton-shimmer` — gradient-based shimmer efekti (pulse yerine)
- `.stagger-children` — 8 kademeli child animasyonu (60ms aralikla)
- `.glass` ve `.glass-card` — glassmorphism efektleri (backdrop-blur + transparent bg)
- `.bar-animate` — progress bar buyume animasyonu
- `prefers-reduced-motion` media query (hareket hassasiyeti)

**Dashboard (`qualifier-dashboard.tsx`):**
- Tab bar: `glass-card` efekti, rounded-xl, active tab beyaz bg + shadow
- Tab content: key-based re-render ile her geciste animasyon
- Container: `animate-[fadeIn_0.3s_ease-out]`
- isPending durumunda opacity transition (200ms)

**Lead Table (`qualifier-lead-table.tsx`):**
- Tablo container: `rounded-2xl border-slate-200/60 shadow-sm`
- Header: gradient arka plan (`from-slate-50 to-slate-100/80`)
- Body: `stagger-children` ile satirlar kademeli gorunur
- Bulk action bar: `backdrop-blur-sm` glassmorphism + `animate-[slideUp_0.2s]`

**Lead Detail (`qualifier-lead-detail.tsx`):**
- Container: `animate-[slideInRight_0.3s_ease-out]` — sagdan kayarak girer
- Geri butonu: `hover:shadow-sm active:scale-95` geri bildirim

**Status Actions (`qualifier-status-actions.tsx`):**
- Modal overlay: `animate-[fadeIn_0.15s_ease-out]`
- Modal body: `animate-[scaleIn_0.2s_ease-out]` + `shadow-2xl`

**Score Breakdown (`qualifier-score-breakdown.tsx`):**
- Progress bar'lar: `.bar-animate` (0'dan hedefe buyume, 700ms)

**Priority Queue (`qualifier-priority-queue.tsx`):**
- Kart listesi: `stagger-children` ile kademeli gorunum
- Kartlar: `rounded-2xl`, `hover:shadow-lg hover:-translate-y-0.5` (200ms)

**Skeleton (`qualifier-skeleton.tsx`) — Tamamen Yeniden Yazildi:**
- `skeleton-shimmer` gradient efekti (animate-pulse yerine)
- `stagger-children` ile kademeli yukleme
- 3 varyant: TableSkeleton, SessionsSkeleton, DetailSkeleton (yeni)
- DetailSkeleton: header + AI summary + info grid + score/BANT layout

**Empty State (`qualifier-empty-state.tsx`) — Yeniden Yazildi:**
- Kucuk icon (24px, gradient bg rounded-2xl)
- `animate-[fadeIn_0.4s_ease-out]`
- CTA butonu: `gradient from-violet-600 to-violet-500` + `shadow-md shadow-violet-200` + hover lift

**Company Selected Panel (`company-selected-panel.tsx`):**
- Qualifier section: `animate-[fadeIn_0.3s]` + `border-slate-200/60`
- Geri butonu: `active:scale-95` feedback

**AI Config Form (`qualifier-ai-config.tsx`):**
- Section header'lar eklendi: "Temel Bilgiler", "Persona & Davranis", "Mesajlar & Zaman", "Kurallar & Bilgi Bankasi"
- Her section arasinda horizontal divider + uppercase etiket
- Header'a gradient icon eklendi (violet AI persona ikonu)

### Degisiklik Yapilan Dosyalar
- `src/app/globals.css` (tamamen yeniden yazildi — animasyonlar, glass, shimmer)
- `src/components/dashboard/companies/qualifier/qualifier-dashboard.tsx` (glass tabs, animations)
- `src/components/dashboard/companies/qualifier/qualifier-lead-table.tsx` (modern table, stagger rows)
- `src/components/dashboard/companies/qualifier/qualifier-lead-detail.tsx` (slideInRight, active:scale)
- `src/components/dashboard/companies/qualifier/qualifier-status-actions.tsx` (modal animation)
- `src/components/dashboard/companies/qualifier/qualifier-score-breakdown.tsx` (bar-animate)
- `src/components/dashboard/companies/qualifier/qualifier-priority-queue.tsx` (stagger, hover lift)
- `src/components/dashboard/companies/qualifier/qualifier-skeleton.tsx` (shimmer, stagger, DetailSkeleton)
- `src/components/dashboard/companies/qualifier/qualifier-empty-state.tsx` (modern redesign)
- `src/components/dashboard/companies/qualifier/qualifier-ai-config.tsx` (section headers, icon)
- `src/components/dashboard/companies/company-selected-panel.tsx` (animations, border tweaks)
