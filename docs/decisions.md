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
