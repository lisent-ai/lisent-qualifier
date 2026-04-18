"""Turkish CHAMP extraction prompt template."""

CHAMP_EXTRACTION_TEMPLATE = """Aşağıdaki müşteri sohbet geçmişini analiz et ve CHAMP parametrelerini JSON olarak çıkar.

## Sohbet Geçmişi
{conversation_history}

{current_champ_section}

## Görev
Sohbetten elde edilen bilgilere göre CHAMP skorunu hesapla. Her kategori 0-25 puan arasında.

Puan kriterleri ve örnekler:

### challenges_score (İhtiyaç netliği ve somutluğu):
- 25: Net proje tanımı + somut gereksinimler ("Antalya'da 3 katlı otel, 40 oda, havuzlu")
- 20: Proje tipi belli + bazı detaylar ("Ticari bina yapmak istiyoruz, yaklaşık 2000 m²")
- 15: Genel ihtiyaç belli ("Villa yaptırmak istiyorum")
- 10: Belirsiz ihtiyaç ("İnşaat düşünüyoruz")
- 5: Çok genel ("Bilgi almak istiyorum")
- 0: Hiç bilgi yok

### authority_score (Karar verme yetkisi):
- 25: Tek karar verici, kendisi belirtti ("Kararı ben veriyorum")
- 20: Karar verici ama onay gerekli ("Eşimle konuşacağım ama genelde ben karar veririm")
- 15: Ortak karar ("Ortağımla birlikte karar vereceğiz")
- 10: Etki sahibi ("Babam için bakıyorum, o karar verecek")
- 5: Belirsiz rol
- 0: Hiç bilgi yok

### money_score (Bütçe netliği ve büyüklüğü):
- 25: Net yüksek bütçe + ödeme yöntemi ("5M TL bütçemiz var, nakit ödeyeceğiz")
- 20: Bütçe aralığı belirtildi ("3-5 milyon arası düşünüyoruz")
- 15: Bütçe var ama belirsiz ("Belli bir bütçemiz var ama detay veremem")
- 10: Finansman planı var ("Kredi kullanmayı düşünüyoruz")
- 5: Bütçeyi konuşmak için erken
- 0: Hiç bütçe bilgisi yok

### prioritization_score (Zaman çizelgesi aciliyeti):
- 25: < 1 ay ("Hemen başlamak istiyoruz", "Bu ay içinde")
- 20: 1-3 ay ("Önümüzdeki 2-3 ay içinde")
- 15: 3-6 ay ("Yaz aylarında başlamayı düşünüyoruz")
- 10: 6-12 ay ("Gelecek yıl planımız var")
- 5: > 12 ay veya belirsiz ("İleride bir zaman")
- 0: Hiç zaman bilgisi yok

Her boyut için güven skoru (0.0-1.0) ver:
- 1.0: Müşteri açıkça belirtti, kesin bilgi
- 0.7: Dolaylı olarak anlaşıldı, makul güven
- 0.4: Tahmin, belirsiz ipuçları
- 0.1: Çok az bilgi, düşük güven

{sector_qualifiers_instruction}

Yanıtını SADECE JSON formatında ver, başka hiçbir şey yazma."""

# Sector qualifier instruction for construction
CONSTRUCTION_QUALIFIERS_INSTRUCTION = """Ayrıca inşaat sektörüne özel bilgileri de çıkar:
- has_land: Müşterinin arsası var mı? (true/false/null)
- permit_status: İmar durumu nedir? ("imarli"/"basvuruldu"/"yok"/null)
- has_architect: Mimar/mühendis ile çalışıyor mu? (true/false/null)
- budget_source: Bütçe kaynağı nedir? ("nakit"/"kredi"/"kurumsal"/null)
- competing_bids: Başka firmalardan teklif alıyor mu? (true/false/null)
- project_sqm: Proje metrekaresi (int/null)"""

# Sector qualifier instruction for real estate (Cyprus investor)
REAL_ESTATE_QUALIFIERS_INSTRUCTION = """Ayrıca emlak yatırım sektörüne özel bilgileri de çıkar:
- has_property_shortlist: İlgilendiği somut proje/villa sayısı (int/null; iki veya daha fazlasını somut isimlendirdiyse >=2)
- financing_ready: Finansman hazırlığı ("cash"/"approved"/"pending"/"none"/null)
- visit_intent: Kıbrıs'a gelip evi yerinde görme niyeti var mı? (true/false/null)
- decision_partner_aligned: Eş/ortak gibi bir karar partneri var ve mutabık mı? (true/false/null)
- exit_strategy_clear: Kiralama/flip/tatil evi gibi net bir çıkış stratejisi var mı? (true/false/null)
- property_type: Mülk tipi ("studio"/"apartment"/"villa"/"land"/null)
- location: İlgilendiği lokasyon (örn. "esentepe"/"girne"/"çatalköy"/null)"""

GENERAL_QUALIFIERS_INSTRUCTION = ""
