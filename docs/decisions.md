# Tasarım kararları

AGENTS.md'nin açık bıraktığı noktalarda verilen kararlar. En basit güvenli seçenek uygulanır.

## 2026-11-05 — M0

- **Repo kökü:** AGENTS.md'deki `launch-check/` yerine mevcut klasör (`viber/`) repo kökü olarak kullanıldı. Yapı birebir aynı.
- **Next.js 16:** `middleware.ts` artık `proxy.ts`. Locale, next-intl'in önerdiği gibi `next/root-params` ile okunuyor (`requestLocale` deprecated). `cacheComponents` scaffold varsayılanı olarak açık bırakıldı.
- **Migration'ları kim uygular:** `api` container'ı açılışta `alembic upgrade head` çalıştırır; `worker` api sağlıklı olana kadar bekler. Ayrı migrate servisi M7'de gerekirse eklenir.
- **Ortam değişkenleri:** Backend ayarları `LC_` önekli (pydantic-settings). Postgres/Caddy kendi değişken adlarını kullanır.
- **PLAY_MIN_TARGET_SDK = 36:** developer.android.com/google/play/requirements/target-sdk (2026-11-05 doğrulandı). Değer `LC_PLAY_MIN_TARGET_SDK` ile config'ten gelir.
- **Worker ağ kısıtı:** Compose tek başına "sadece dış web'e çıkış" kuralını uygulayamaz (worker'ın Redis/Postgres'e de erişmesi gerekiyor). SSRF koruması uygulama katmanında (M2). Prod'da host firewall/egress kuralı ile private aralıklara çıkış ayrıca engellenecek (M7).
- **Caddy logları:** IP alanları log filtresiyle siliniyor (veri minimizasyonu).
- **Ruff RUF001-003 kapalı:** Türkçe karakterler yorum/metinlerde bilinçli kullanılıyor.
- **JDK 21:** `python:3.12-slim` artık Debian trixie tabanlı ve orada openjdk-17 paketi yok. AGENTS.md "JDK 17+" dediği için 21 kullanılıyor. apktool 3.0.3, jadx 1.5.6 (2026-11-05 itibarıyla en son sürümler). apktool 3.x'in CLI farkları M4'te doğrulanacak.
- **`strings` aracı yok:** Flutter `libapp.so` string çıkarımı Python'da yapılacak (binutils bağımlılığı gereksiz).
- **Dev'de Caddy düz HTTP:** `DOMAIN=http://localhost` (dev compose). Prod'da gerçek alan adı ile otomatik HTTPS.

## 2026-11-05 — M1

- **Kısa secret maskeleme:** AGENTS.md "ilk 6 + son 4" diyor. 20 karakterden kısa değerlerde bu, secret'ın yarısından fazlasını açığa çıkarır; bu yüzden kısa değerlerde sadece ilk 2 karakter gösterilir (`ab…`).
- **Taranan URL vs. saklanan:** DB'ye sadece normalize origin (`target_label`) yazılır. Taranacak tam URL (origin + path; query ve fragment atılır) sadece arq job argümanı olarak Redis'e gider. arq job argümanlarını loglar; URL'de query olmadığı için kişisel veri/token loglanmaz.
- **Takılı kalan taramalar:** Worker çökerse tarama `running`'de kalır ve frontend sonsuza dek bekler. 5 dakikada bir çalışan cron, `stale_scan_after_s` (15 dk) süresini aşan queued/running taramaları `failed / scan_stale` yapar. Job'lar tekrar denenmez (`max_tries=1`); kullanıcı yeniden tarar.
- **"Geçti" listesi:** GET yanıtındaki `passed`, hedefe uygulanan ve bulgu üretmeyen check'lerdir. Hata veren check'ler (`meta.checks_failed`) geçti sayılmaz; bu iç alan yanıtta gizlenir.
- **Ağırlıksız kategoriler:** `score_weights`'te olmayan bir kategoride bulgu çıkarsa kategori skoru gösterilir ama genel skoru etkilemez. Faz 1 check'leri security/prod_readiness kategorilerine atanmalı.
- **Yuvarlama:** Skorlar yarım yukarı yuvarlanır (98.5 → 99). Python'un `round()` fonksiyonu kullanılmaz.
- **APK endpoint'i:** `POST /api/scans/apk` upload güvenliğiyle birlikte M4'te eklenecek.
- **Testler SQLite (aiosqlite) üzerinde** çalışır (CI'da Postgres gerekmez). Model ile migration uyumu `alembic check` ile gerçek Postgres'te doğrulandı.
