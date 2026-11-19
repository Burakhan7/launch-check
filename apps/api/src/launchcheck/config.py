"""Uygulama konfigürasyonu. Tüm ayarlar ortam değişkenlerinden okunur (LC_ öneki)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LC_", env_file=".env", extra="ignore")

    # Genel
    env: str = "dev"
    public_base_url: str = "http://localhost:3000"
    log_level: str = "INFO"

    # Altyapı
    database_url: str = "postgresql+asyncpg://launchcheck:launchcheck@localhost:5432/launchcheck"
    redis_url: str = "redis://localhost:6379/0"

    # Saklama
    report_retention_days: int = 7
    # Bu süreden uzun queued/running kalan tarama "yarıda kaldı" sayılır (worker çöktüyse)
    stale_scan_after_s: int = 900

    # Skorlama: kategori ağırlıkları (Faz 1)
    score_weights: dict[str, float] = Field(
        default_factory=lambda: {"security": 0.5, "prod_readiness": 0.25, "conversion": 0.25}
    )

    # Worker
    worker_max_jobs: int = 4
    apk_scan_timeout_s: float = 480.0

    # Rate limit (IP başına, saatlik)
    rate_limit_web_per_hour: int = 10
    rate_limit_apk_per_hour: int = 3
    rate_limit_ip_salt: str = "change-me"

    # Web tarama limitleri
    http_connect_timeout_s: float = 5.0
    http_request_timeout_s: float = 15.0
    web_scan_timeout_s: float = 90.0
    max_redirects: int = 5
    max_html_bytes: int = 5 * 1024 * 1024
    max_js_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 50 * 1024 * 1024
    max_js_files: int = 60
    js_fetch_concurrency: int = 6
    probe_max_bytes: int = 1024 * 1024  # /.env, /.git/config gibi tekil dosya kontrolleri
    dns_timeout_s: float = 5.0
    bot_user_agent: str = "LaunchCheckBot/1.0 (+https://launchcheck.example/bot)"

    # İç sayfa taraması: ana sayfa + en fazla bu kadar iç sayfa (LC_WEB_MAX_INTERNAL_PAGES)
    web_max_internal_pages: int = 10
    # Bu sayfaların en az bu kadarı menü dışı (sayfa içi) linklere ayrılır: gönderi, ürün
    # detayı gibi sayfalar da taransın diye. İçerik linki azsa yer menü linkleriyle dolar.
    web_internal_content_link_slots: int = 3
    # İç sayfa çekimleri için süre sınırı. Dolarsa elde olan sayfalarla devam edilir; böylece
    # toplam web tarama süresi (web_scan_timeout_s) aşılıp tarama başarısız olmaz.
    web_internal_pages_timeout_s: float = 30.0
    # web.render.empty_server_html: ana içerik bu kelime sayısının altındaysa "boş" sayılır
    web_min_main_content_words: int = 40
    # Login/hesap gibi sunucuda içerik beklenmeyen yollar (yol parçası olarak eşleşir: /tr/login)
    web_render_skip_paths: list[str] = Field(
        default_factory=lambda: [
            "/login",
            "/signin",
            "/register",
            "/signup",
            "/account",
            "/cart",
            "/checkout",
            "/admin",
            "/dashboard",
        ]
    )
    # web.infra.origin_exposed: hosting platformlarının varsayılan alan adları (alt alan adları
    # eşleşir). Supabase, Firebase Storage, Cloudinary, S3 gibi beklenen depolama/CDN'ler YOK.
    web_platform_default_domains: list[str] = Field(
        default_factory=lambda: [
            "up.railway.app",
            "railway.app",
            "onrender.com",
            "herokuapp.com",
            "fly.dev",
            "vercel.app",
            "netlify.app",
            "azurewebsites.net",
            "appspot.com",
            "run.app",
            "pages.dev",
            "workers.dev",
            "replit.app",
            "repl.co",
            "lovable.app",
            "ondigitalocean.app",
            "koyeb.app",
        ]
    )
    # web.seo.duplicate_title_description: en az bu kadar (canonical'a göre tekil) sayfa gerekir
    web_duplicate_min_pages: int = 3
    web_short_description_chars: int = 50
    # web.privacy.third_party_form_embed: 3. parti form servisleri ("host" veya "host/yol-öneki").
    # Host eşleşmesi alt alan adlarını da kapsar (typeform.com → x.typeform.com).
    web_third_party_form_hosts: list[str] = Field(
        default_factory=lambda: [
            "forms.monday.com",
            "docs.google.com/forms",
            "forms.gle",
            "typeform.com",
            "jotform.com",
            "tally.so",
            "forms.office.com",
            "forms.microsoft.com",
            "surveymonkey.com",
            "airtable.com/embed",
            "js.hsforms.net",
            "share.hsforms.com",
            "formspree.io",
            "paperform.co",
            "fillout.com",
        ]
    )
    # web.files.api_docs_exposed: herkese açık API dokümantasyonu (Swagger/OpenAPI/ReDoc) yolları
    web_api_doc_paths: list[str] = Field(
        default_factory=lambda: [
            "/openapi.json",
            "/swagger.json",
            "/api/openapi.json",
            "/api/swagger.json",
            "/v3/api-docs",
            "/api-docs",
            "/docs",
            "/api/docs",
            "/swagger-ui.html",
            "/swagger",
            "/redoc",
        ]
    )
    # web.links.malformed_youtube: bu sayıdan itibaren önem medium
    web_youtube_medium_threshold: int = 4
    # web.a11y.img_missing_alt: alt'sız görsel sayısı >= bu VEYA oranı > aşağıdaki → bulgu
    web_img_alt_min_missing: int = 5
    web_img_alt_max_ratio: float = 0.2

    # Tespit anahtar kelimelerinin dilleri: launchcheck/keywords/<dil>.yaml
    keyword_languages: list[str] = Field(default_factory=lambda: ["tr", "en"])

    # has_live_media: medya ve iletişim kelimesi arasında en fazla bu kadar kelime
    web_live_media_window_words: int = 8

    # web.downloads.non_release_build
    web_download_extensions: list[str] = Field(
        default_factory=lambda: [
            ".exe",
            ".msi",
            ".msix",
            ".appx",
            ".dmg",
            ".pkg",
            ".apk",
            ".aab",
            ".ipa",
            ".appimage",
            ".deb",
            ".rpm",
        ]
    )
    # Arşivler sadece link metni indirme/kurulum dediğinde uygulama dosyası sayılır
    web_download_archive_extensions: list[str] = Field(
        default_factory=lambda: [".zip", ".tar.gz", ".tgz", ".7z"]
    )
    # Dosya adı/yol token'ı (- _ . / ile ayrılmış, tam eşleşme). "latest", "setup",
    # "installer" bilinçli olarak yok: meşru sürüm adlarıdır.
    web_nonrelease_tokens: list[str] = Field(
        default_factory=lambda: [
            "test",
            "testing",
            "dev",
            "debug",
            "beta",
            "alpha",
            "staging",
            "nightly",
            "canary",
            "snapshot",
            "rc",
            "preview",
            "internal",
            "local",
            "sandbox",
        ]
    )
    # Ana sayfada sıradaki ilk bu kadar indirme linki birincil CTA sayılır
    web_primary_download_links: int = 2

    # web.render.route_serves_root_shell: ana içerik benzerliği (kelime 3-shingle Jaccard)
    web_shell_similarity_threshold: float = 0.9
    web_shell_min_signals: int = 2

    # web.privacy.internal_role_exposed (dilden bağımsız: kod/veri adları)
    web_role_enum_names: list[str] = Field(
        default_factory=lambda: [
            "SUPER_ADMIN",
            "SUPERADMIN",
            "ADMIN",
            "ROOT",
            "OWNER",
            "SYSTEM",
            "STAFF",
            "MODERATOR",
            "DEVELOPER",
            "SUPERUSER",
        ]
    )
    web_role_json_keys: list[str] = Field(
        default_factory=lambda: [
            "role",
            "roles",
            "permissions",
            "is_admin",
            "isadmin",
            "is_superuser",
            "issuperuser",
            "is_staff",
            "isstaff",
            "superadmin",
            "is_super_admin",
        ]
    )
    web_sensitive_user_json_keys: list[str] = Field(
        default_factory=lambda: [
            "email",
            "e_mail",
            "phone",
            "phone_number",
            "phonenumber",
            "mobile",
            "password",
            "password_hash",
            "passwordhash",
            "hash",
            "salt",
            "token",
            "access_token",
            "accesstoken",
            "refresh_token",
            "refreshtoken",
            "api_key",
            "apikey",
            "secret",
            "ip",
            "ip_address",
            "last_ip",
        ]
    )

    # web.conversion.empty_public_listing
    web_min_listing_items: int = 6
    web_min_social_proof_count: int = 20

    # web.conversion.keyword_stuffing
    web_stuffing_ngram_min: int = 3
    web_stuffing_ngram_max: int = 6
    web_stuffing_min_repeats: int = 4
    web_stuffing_min_density: float = 0.03
    # Daha kısa sayfalarda (fiyat kartları gibi) tekrar doğaldır
    web_stuffing_min_words: int = 100
    # Öbekler sadece bu uzunluktaki metin bloklarında sayılır (kart açıklaması/altyazı değil)
    web_stuffing_min_block_words: int = 12
    web_meta_keywords_max: int = 10
    web_meta_keywords_max_variants: int = 4

    # web.session.guest_account_per_request: ana sayfa bu kadar kez daha çerezsiz çekilir
    web_cookieless_extra_samples: int = 2
    # Yaş/kriz bilgisi aramak için footer'daki hakkında/şartlar/yardım sayfalarından en fazla
    # bu kadarı ayrıca çekilir (taranmış sayfalar tekrar çekilmez)
    web_extra_info_pages: int = 3
    # web.privacy.exif_in_uploads: kullanıcı yüklemesi gibi görünen görseller
    web_exif_max_images: int = 10
    web_exif_max_image_bytes: int = 5 * 1024 * 1024
    web_exif_upload_path_markers: list[str] = Field(
        default_factory=lambda: [
            "/uploads/",
            "/upload/",
            "/storage/",
            "/media/",
            "/user/",
            "/users/",
            "/posts/",
            "/attachments/",
            "/user-content/",
        ]
    )
    # Logo, ikon, tema görselleri (yolun herhangi bir yerinde geçerse atlanır)
    web_exif_skip_path_markers: list[str] = Field(
        default_factory=lambda: [
            "/theme/",
            "/themes/",
            "/assets/",
            "/static/",
            "/icons/",
            "/icon/",
            "favicon",
            "logo",
            "/emoji/",
        ]
    )
    # web.content.test_data_in_production: tam kelime eşleşmesi (Türkçe karakterler
    # sadeleştirilmiş). "foo"/"bar" sadece başlığın tamamıysa sayılır (aşağıda).
    web_test_data_keywords: list[str] = Field(
        default_factory=lambda: [
            "test",
            "testing",
            "deneme",
            "denemee",
            "asdf",
            "qwerty",
            "lorem ipsum",
            "dummy",
            "sample",
            "ornek urun",
            "test urun",
            "test123",
            "aaa",
            "xxx",
        ]
    )
    web_test_data_exact_only: list[str] = Field(default_factory=lambda: ["foo", "bar"])
    # Bu ifadeleri içeren metinler test verisi sayılmaz
    web_test_data_exceptions: list[str] = Field(
        default_factory=lambda: [
            "hiz testi",
            "hizli okuma testi",
            "test sonuclari",
            "test sonucu",
            "testimonial",
            "a/b test",
            "ab test",
            "speed test",
            "test drive",
            "kisilik testi",
            "seviye testi",
            "online test",
            "covid test",
            "pcr test",
            "test et",
            "ucretsiz dene",
        ]
    )
    # web.ids.sequential_public_ids: hassas yol parçaları / query parametreleri
    web_sequential_id_names: list[str] = Field(
        default_factory=lambda: [
            "room",
            "rooms",
            "oda",
            "odalar",
            "canli",
            "chat",
            "chats",
            "sohbet",
            "message",
            "messages",
            "mesaj",
            "mesajlar",
            "conversation",
            "conversations",
            "inbox",
            "gelen",
            "order",
            "orders",
            "siparis",
            "siparisler",
            "invoice",
            "invoices",
            "fatura",
            "user",
            "users",
            "kullanici",
            "profile",
            "ticket",
            "tickets",
            "alinti",
            "dm",
        ]
    )
    web_sequential_id_min_distinct: int = 3
    web_sequential_id_max_value: int = 100_000

    # APK yükleme / analiz
    max_apk_bytes: int = 150 * 1024 * 1024
    max_apk_uncompressed_bytes: int = 1024 * 1024 * 1024
    max_apk_entries: int = 50_000
    max_apk_compression_ratio: float = 200.0
    # API ve worker'ın ortak volume'u. Worker'a sadece dosya adı gider.
    upload_dir: str = "/var/lib/launchcheck/uploads"
    # Worker çöktüyse geride kalan yüklemeler bu süreden sonra cron ile silinir
    upload_orphan_after_s: int = 3600
    java_max_heap: str = "1536m"
    jadx_threads: int = 2
    # String kaynaklarının toplam boyut limiti (bellek koruması)
    apk_max_string_bytes: int = 200 * 1024 * 1024
    apk_max_asset_bytes: int = 5 * 1024 * 1024  # tek bir asset/raw dosyası
    apktool_timeout_s: float = 120
    jadx_timeout_s: float = 240
    scan_tmp_dir: str = Field(default="/tmp/launchcheck")  # noqa: S108

    # Google Play minimum targetSdkVersion — değeri .env'den gelir, bkz. .env.example
    play_min_target_sdk: int = 36

    # --- Faz 2: derin tarama (docs/PHASE2.md §13) ---
    deep_max_concurrent: int = 1
    # Bekleyen (queued) Faz 2 sayısı bunu aşarsa yeni istek `queue_full`
    deep_queue_max: int = 10
    deep_scan_timeout_s: float = 1200  # sert sınır 20 dk
    deep_max_pages: int = 25
    deep_max_subdomains: int = 8
    deep_max_competitors: int = 3
    deep_competitor_max_pages: int = 6
    # Şerit bütçeleri: aşınca modül elindekiyle biter (meta.deep.partial_modules)
    deep_budget_browser_s: float = 420
    deep_budget_lighthouse_s: float = 240
    deep_budget_network_s: float = 180
    deep_budget_llm_s: float = 360
    # Tek bir modülün bütçesi (şerit bütçesinden de sınırlıdır)
    deep_budget_module_s: float = 420
    # Faz 1 raporu bundan eskiyse derin tarama başlatılamaz
    deep_parent_max_age_h: int = 24
    rate_limit_deep_per_day: int = 3
    # Geçmiş veri yokken ETA için varsayılan tarama süresi
    deep_default_duration_s: int = 840
    # Kuyrukta bundan uzun bekleyen Faz 2 taraması "yarıda kaldı" sayılır
    deep_queued_stale_after_s: int = 6 * 3600
    deep_events_page_size: int = 200
    deep_max_asset_bytes: int = 3 * 1024 * 1024
    # Asset klasöründe DB karşılığı olmayan dosyalar bu süreden sonra silinir
    deep_asset_orphan_after_s: int = 3600
    # PageSpeed Insights / CrUX (ücretsiz kota); boşsa saha verisi atlanır
    google_api_key: str = ""
    llm_base_url: str = "http://ollama:11434"
    llm_model: str = ""  # P8'de eval ile seçilir
    llm_timeout_s: float = 150
    llm_max_output_tokens: int = 900
    score_weights_deep: dict[str, float] = Field(
        default_factory=lambda: {
            "security": 0.4,
            "prod_readiness": 0.2,
            "performance": 0.15,
            "conversion": 0.25,
        }
    )
    benchmark_cache_ttl_h: int = 24
    assets_dir: str = "/var/lib/launchcheck/assets"


@lru_cache
def get_settings() -> Settings:
    return Settings()
