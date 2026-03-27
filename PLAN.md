"Sen kıdemli bir yazılım mimarı ve sistem tasarım uzmanısın. İnşaat sektörü için ai-lead-qualifier adında bağımsız bir mikroservis mimarisi tasarlayacağız.

Şu an sadece planlama aşamasındayız. Hiçbir kod yazma. Aşağıdaki iş kurallarını (business logic) ve donanım/ortam kısıtlamalarını dikkatlice analiz et. Benden onay almadan kodlamaya geçmeyeceksin; önce bana hangi teknolojileri seçeceğini, nasıl bir mimari kuracağını, Domain-Driven Design (Alan Odaklı Tasarım) tabanlı klasör yapısını ve geliştirme adımlarını içeren bir teknik doküman sun.

1. Donanım ve Ortam Kısıtlamaları:

Sunucu: GPU'su bulunmayan, ancak çok çekirdekli asimetrik işlemciye (3D V-Cache destekli) ve yüksek RAM kapasitesine sahip kiralık bir fiziksel sunucu (bare-metal). (Hetzner AX102 CPU'da çalışacak)

Konteynerizasyon: Uygulamanın ana API servisi ve geçici durum/oturum (state/session) yönetimi Docker Compose içinde izole olarak çalışacak.

Yerel LLM Motoru: İşlemci optimizasyonu (NUMA düğümleri ve V-Cache çekirdek kilitlemesi) yapabilmek adına, arka planda çalışacak yerel yapay zeka çıkarım motoru Docker içinde veya ekstra bir sarmalayıcı (wrapper) katmanında çalışmayacak. Doğrudan ana makinede (host) çıplak (native HTTP server) olarak ayağa kaldırılacak. Docker içindeki API, bu motora ağ üzerinden erişecek.

2. Beklenen Mimari Nitelikler:

Tamamen asenkron, olay güdümlü (event-driven) ve durumsuz (stateless) bir backend yapısı.

Yüksek hızda okuma/yazma yapabilen bellek içi (in-memory) bir oturum ve skor takip mekanizması.

Ön yüz sohbeti için dışarıdan hizmet alınan, gecikme süresi (latency) sıfıra yakın ultra hızlı bir Bulut LLM servisi.

Dışarıdan gelen formların ve yapay zekadan dönen JSON çıktılarının katı şema doğrulamasından (schema validation) geçirilmesi.

3. İş Kuralları ve Veri Akışı (Business Logic):

Girdi: Sosyal medya formlarından gelen müşteri verileri bir webhook ile sisteme düşer.

Ön Puanlama (Warm Start): Form verisine göre kural tabanlı bir başlangıç skoru hesaplanır. Skor hedef barajın üstündeyse bot hiç devreye girmez; veri anında satış ekibinin CRM'ine iletilir.

Gerekçe Raporu (Reasoning): Skor gri alandaysa (örn: ortalama bir bütçe), veri yerel LLM motoruna gönderilir. LLM, "Müşteri neden bu skoru aldı ve satışı kapatmak için telefonda ne konuşulmalı?" sorusuna yanıt veren kısa bir JSON rapor (reasoning_report) üretir.

AI Chat Yönlendirmesi: Skor barajın altındaysa müşteri Bulut LLM destekli sohbete alınır. Form verileri ve reasoning_report, bu sohbet modelinin sistem prompt'una bağlam (context) olarak gizlice eklenir.

Asenkron Veri Ayıklama: Sohbet akarken, ana iş parçacığını (main thread) bloklamadan, sohbet geçmişi belirli aralıklarla yerel LLM motoruna gönderilir. Yerel model BANT (Bütçe, Yetki, İhtiyaç, Zaman) parametrelerini JSON olarak çeker, skoru ve gerekçe raporunu günceller.

Devir (Hand-off): Güncel skor hedef barajı aştığında, Bulut LLM'e(Groq olacak) sohbeti sonlandırma talimatı verilir. Tüm paket (Müşteri bilgisi + BANT JSON verisi + Nihai Skor + Gerekçe Raporu) CRM'e bir webhook ile aktarılır.

Görevin: Bu kısıtlamaları okuduktan sonra bana sağlam, aşırı detaylı, 2026 web güncel verilerle destekli modern ve hata toleranslı (fail-safe) bir sistem planı çıkar. Seçtiğin teknoloji yığınını (Tech Stack) gerekçeleriyle belirt. Onayımı bekle."
