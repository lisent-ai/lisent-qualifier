"Mimari planın donanım analizi (NUMA/V-Cache pinleme) ve SSE streaming kararları harika. Ancak production ortamında sistemi hantallaştıracak bazı 'over-engineering' (aşırı mühendislik) kararları ve eski nesil model seçimleri var. Lütfen planı şu 5 kritere göre derhal güncelle ve ardından kodlamaya Faz 1'den başlayalım:

1. Model ve Runtime Güncellemeleri (2026 Standartları):

Cloud LLM: Groq üzerinde llama-3.3 yerine llama-4-70b-instruct (veya güncel Llama 4) kullanılacak ya da araştır Groq içinde daha iyisi varsa onu kullanacağız..

Local LLM: Qwen2.5 yerine JSON çıkarma ve reasoning konusunda çok daha stabil olan Qwen3.5-9B (GGUF) modeli kullanılacak veya araştır daha iyisi varsa kullanabailceğimiz onu kullanacağız.

Runtime: Python 3.12 yerine GIL kilidi olmayan (Free-threaded) Python 3.13+ kullanılacak.

2. ARQ Worker İptali (Over-Engineering):

Sistemde ARQ ve ayrı bir Redis Worker süreci İSTEMİYORUM. Arka plan BANT çıkarma işi (extraction), Python 3.13'ün no-GIL avantajı kullanılarak doğrudan FastAPI'nin yerleşik BackgroundTasks özelliği (veya standart asyncio task'ları) ile çözülecek. Karmaşayı azalt.

3. GBNF Grammar İptali:

Qwen 3.5 serisi yapılandırılmış JSON çıktısında zaten kusursuzdur. Modelin reasoning yeteneğini bozan GBNF grammar dosyası kullanımını plandan çıkar. JSON formatlaması sadece system prompt ve Pydantic fallback (Tenacity retry) ile sağlanacak.

4. Pydantic Strict Mod Kapatılması:

Dışarıdan gelen webhook verilerinde (yaş, bütçe vs.) string/int karmaşasını çözmek için TypeAdapter'larda strict=True zorlamasını kaldır. Type Coercion'a (otomatik tip dönüşümüne) izin ver.

5. Skor Barajı Mantığının Düzeltilmesi:

Plandaki skorlama mantığı hatalı. Yeni mantık: Müşterinin başlangıç skoru 80'in altındaysa müşteri Groq ile SOHBETE alınır. Sohbet sürdükçe BANT güncellenir. Skor 80'i aştığı an reasoning_report üretilir ve CRM'e (Handoff) gönderilir. Sohbet sadece 50 altı leadler için değil, 80 altındaki tüm leadleri ısıtmak için kullanılacak.