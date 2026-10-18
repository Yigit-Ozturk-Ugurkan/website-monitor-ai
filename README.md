# Website Monitor AI

Web sayfalarındaki değişiklikleri zamanlanmış kontrollerle izleyen, değişikliklerin seçilen konuyla ilişkisini analiz eden ve bildirim üreten bir web uygulamasıdır.

Standart web takibine ek olarak mevzuat değişikliklerini referans metinlerle karşılaştırmaya yönelik bir iş akışı içerir.

## Özellikler

- Tam sayfa, otomatik bölüm ve kullanıcı tarafından seçilen bölüm takibi.
- Günlük, hafta içi ve haftalık kontrol planları.
- Önceki ve güncel içeriklerin saklanması ve karşılaştırılması.
- Eklenen ve kaldırılan satırların tespiti.
- Gereksiz değişiklikleri azaltmaya yönelik gürültü filtreleme.
- Gemini destekli konu ilişkisi, önem ve özet analizi.
- Mevzuat referanslarının manuel seçimi veya otomatik keşfi.
- Mevzuat metinlerinde madde ayrıştırma ve karşılaştırma.
- Uygulama içi bildirimler ve SMTP yapılandırıldığında e-posta gönderimi.
- Son çalıştırma ve hata durumlarının arayüzde gösterilmesi.

## Teknolojiler

| Katman | Teknolojiler |
| --- | --- |
| Backend | Python, FastAPI |
| Veri | PostgreSQL, SQLAlchemy |
| İçerik alma | HTTPX, BeautifulSoup |
| Yapay zekâ | Google Gemini |
| Frontend | React, JavaScript, Vite |
| Bildirim | SMTP |

## Çalışma Yaklaşımı

İlk kontrol başlangıç içeriğini oluşturur. Sonraki kontrollerde seçilen kapsamın güncel metni önceki kayıtla karşılaştırılır. Değişiklikler konu ve AI analizinden geçirilerek ilgili sonuçlar ve bildirimler oluşturulur.

Mevzuat takibinde ayrıca referans metin seçimi veya keşfi, madde eşleştirme ve karşılaştırma aşamaları kullanılır. Eşleşme bulunamaması ve analiz hataları ayrı durumlar olarak raporlanır.

## Proje Yapısı

- `app/api/`: API uçları
- `app/services/`: İçerik alma, karşılaştırma, AI ve bildirim servisleri
- `app/models/`: Veritabanı modelleri
- `app/repositories/`: Veri erişimi
- `app/core/`: Yapılandırma, veritabanı ve zamanlayıcı
- `frontend/`: Kullanıcı arayüzü
- `tests/`: Otomatik testler, manuel kontroller ve geçiş betikleri
- `test_site/`: Yerel test sayfaları

## Yapılandırma

Ayar örnekleri `.env.example` dosyasındadır. PostgreSQL bağlantısı ve Gemini anahtarı yapılandırılmalıdır. E-posta bildirimi ayrıca SMTP ayarları gerektirir.

## Testler

Otomatik test çalıştırıcısı:

    python -m tests.run_automated_suite

Canlı kabul testleri ayrı çalıştırılır ve dış servisler gerektirebilir. `manual_*` ve `migrate_*` betikleri otomatik test değildir.

## Mevcut Sınırlar

- Yerel veya erişimi kısıtlanmış geliştirme kullanımı hedeflenir; API kimlik doğrulaması bulunmaz.
- Kullanıcı URL’leri için özel ağ erişimini engelleyen ek kontroller gerekir.
- İçerik getirici HTML sayfaları destekler; PDF desteği bulunmaz.
- AI sonuçları model ve kaynak kalitesine bağlıdır; mevzuat yorumları insan incelemesi gerektirir.
- Zamanlayıcı uygulama sürecinde çalışır; çoklu worker kullanımı ayrıca koordinasyon gerektirir.
