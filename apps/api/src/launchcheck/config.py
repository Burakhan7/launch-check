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
    # P11 ölçümüyle ayarlandı (docs/decisions.md, 8 site): en uzun şerit 720 sn = 12 dk < 15 dk.
    # Tarayıcı şeridi: render 420 + gizlilik 90 + huni 75 + kıyas 120 (P9: 540, P11: 720)
    deep_budget_browser_s: float = 720
    # Lighthouse: ölçümde en uzun 212 sn (240'ın %88'i) → pay için 300
    deep_budget_lighthouse_s: float = 300
    # Ağ şeridi: dış görünürlük (150) + linkler (510, render beklemesi dahil)
    deep_budget_network_s: float = 660
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
    # Tarayıcı: sayfa başına zaman sınırı ve egress proxy'den geçen toplam yanıt baytı
    deep_page_timeout_s: float = 20
    deep_proxy_max_total_bytes: int = 200 * 1024 * 1024
    # Render: "load + bu kadar sessizlik veya en fazla deep_render_max_wait_s" (networkidle değil)
    deep_render_quiet_s: float = 2
    deep_render_max_wait_s: float = 10
    deep_render_concurrency: int = 3
    # Render modülünün payı (tarayıcı şeridi: render → gizlilik → [P7 huni] → [P9 kıyas])
    # P11: 240→420. 240 sn'de ağır sitelerde (Wix, Shopify) 25 sayfanın 10-16'sı açılabiliyor ve
    # ek yoklamalar (404, derin link, yönetim) atlanıyordu; kapsam meta.render'da görünür.
    deep_budget_render_s: float = 420
    # Ana sayfa + en fazla bu kadar kilit sayfa (fiyat > kayıt > ürün > ilk menü linki):
    # 5 genişlikte ölçülür, ekran görüntüsü ve JS kapalı görüntü alınır
    deep_key_pages: int = 2
    deep_measure_widths: list[int] = Field(default_factory=lambda: [320, 375, 768, 1024, 1440])
    deep_admin_route_probes: int = 5
    # --- Hız modülü (P4): Lighthouse + CrUX ---
    deep_lighthouse_bin: str = "lighthouse"
    deep_chrome_path: str = ""  # boşsa Playwright'ın kurduğu Chromium bulunur
    # Mobil ölçüm tekrarı (ortanca alınır): ana sayfa ve kilit sayfa için ayrı ayrı
    deep_lighthouse_runs: int = 3
    deep_lighthouse_desktop_runs: int = 1
    deep_lighthouse_run_timeout_s: float = 90
    deep_lighthouse_max_wait_for_load_ms: int = 35000
    deep_lighthouse_max_report_bytes: int = 40 * 1024 * 1024
    # Kilit sayfa render modülünden gelir; en fazla bu kadar beklenir
    deep_key_page_wait_s: float = 90
    deep_crux_endpoint: str = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
    deep_crux_timeout_s: float = 10
    # Eşikler: Google'ın Core Web Vitals "iyi / geliştirilmeli / kötü" sınırları (web.dev/vitals)
    deep_perf_lcp_good_ms: float = 2500
    deep_perf_lcp_poor_ms: float = 4000
    deep_perf_cls_good: float = 0.1
    deep_perf_cls_poor: float = 0.25
    deep_perf_inp_good_ms: float = 200
    deep_perf_inp_poor_ms: float = 500
    deep_perf_blank_screen_ms: float = 3000
    # Filmstrip karesinde içerik sayılan piksel oranı (altı "boş ekran")
    deep_perf_blank_content_ratio: float = 0.015
    deep_perf_filmstrip_max_ms: float = 5000
    deep_perf_unused_js_ratio: float = 0.5
    deep_perf_unused_js_min_bytes: int = 200 * 1024
    deep_perf_image_min_waste_bytes: int = 100 * 1024
    deep_perf_unsized_images_min: int = 3
    deep_perf_uncompressed_min_bytes: int = 10 * 1024  # tek dosya
    deep_perf_uncompressed_total_bytes: int = 30 * 1024
    deep_perf_short_cache_s: int = 24 * 3600
    deep_perf_short_cache_min_bytes: int = 100 * 1024
    deep_perf_third_party_bytes: int = 500 * 1024
    deep_perf_third_party_main_thread_ms: float = 1000
    # --- Dış görünürlük modülü (P5): crt.sh, DNS, Wayback, RDAP, sertifika ---
    deep_budget_external_s: float = 150
    # Servis başına toplam istek süresi (sn). Wayback CDX büyük sitelerde ~60 sn sürebiliyor
    # (2026-11-23 ölçümü); crt.sh sık sık 502 döner, o zaman Cert Spotter'a geçilir.
    deep_external_timeouts_s: dict[str, float] = Field(
        default_factory=lambda: {
            "crtsh": 30,
            "certspotter": 20,
            "wayback": 60,
            "rdap": 15,
            "youtube": 10,
        }
    )
    deep_external_max_response_bytes: int = 8 * 1024 * 1024
    deep_crtsh_endpoint: str = "https://crt.sh/"
    deep_certspotter_endpoint: str = "https://api.certspotter.com/v1/issuances"
    deep_wayback_endpoint: str = "https://web.archive.org/cdx/search/cdx"
    deep_rdap_endpoint: str = "https://rdap.org/domain/"
    # Tüm taramalar için ortak istek sınırı (Redis): servis → [istek, pencere sn]. Aşılırsa o
    # parça atlanır. Cert Spotter kimliksiz kullanımda saatte 10 alt alan adlı sorgu verir.
    deep_external_rate_limits: dict[str, list[int]] = Field(
        default_factory=lambda: {
            "crtsh": [10, 60],
            "certspotter": [10, 3600],
            "wayback": [15, 60],
            "rdap": [30, 60],
            "youtube": [120, 60],
        }
    )
    deep_dns_timeout_s: float = 5
    # crt.sh'tan gelen adlardan en fazla bu kadarının DNS'i çözülür (öncelikliler önce)
    deep_subdomain_max_candidates: int = 60
    deep_subdomain_dns_concurrency: int = 10
    deep_subdomain_scan_concurrency: int = 3
    deep_subdomain_scan_timeout_s: float = 45
    deep_subdomain_max_js_files: int = 20
    deep_subdomain_max_total_bytes: int = 15 * 1024 * 1024
    # Ad içinde geçerse önce taranır (unutulmuş ortam olma ihtimali yüksek). Tespit için tek
    # başına yeterli değildir: alt alan adı bulgusu ancak Faz 1 check'i bir sorun bulursa çıkar.
    deep_subdomain_priority_words: list[str] = Field(
        default_factory=lambda: [
            "staging",
            "stage",
            "dev",
            "test",
            "beta",
            "admin",
            "panel",
            "api",
            "old",
            "backup",
            "demo",
            "preview",
            "eski",
            "yedek",
            "deneme",
            "yonetim",
        ]
    )
    # Alt alan adlarında çalışan Faz 1 check'leri ("*" ile biten önek demek)
    deep_subdomain_check_ids: list[str] = Field(
        default_factory=lambda: [
            "web.files.*",
            "web.secrets.*",
            "web.bundle.localhost_urls",
            "web.https.missing",
            "web.seo.noindex_left",
        ]
    )
    # Sadece bilgi: tek başına alt alan adı bulgusu üretmez (staging'in noindex olması iyidir)
    deep_subdomain_info_checks: list[str] = Field(default_factory=lambda: ["web.seo.noindex_left"])
    # Ortak barındırma alan adları: hedef bunların altındaysa (ör. app.lovable.app) alt alan
    # adları, e-posta DNS'i ve alan adı kaydı platformundur, kullanıcının değil → atlanır.
    # Public Suffix List'in "private" bölümü ayrıca kontrol edilir; bu liste ek güvencedir.
    deep_shared_host_suffixes: list[str] = Field(
        default_factory=lambda: [
            "lovable.app",
            "lovableproject.com",
            "bolt.host",
            "netlify.app",
            "vercel.app",
            "pages.dev",
            "workers.dev",
            "github.io",
            "herokuapp.com",
            "onrender.com",
            "fly.dev",
            "replit.app",
            "repl.co",
            "web.app",
            "firebaseapp.com",
            "glitch.me",
            "surge.sh",
            "azurewebsites.net",
            "wixsite.com",
            "webflow.io",
            "framer.website",
            "framer.app",
            "carrd.co",
            "myshopify.com",
            "blogspot.com",
            "wordpress.com",
            "squarespace.com",
            "notion.site",
            "up.railway.app",
            "bubbleapps.io",
            "softr.app",
            "expo.app",
        ]
    )
    deep_spf_max_lookups: int = 10  # RFC 7208 §4.6.4
    deep_wayback_max_rows: int = 5000
    deep_wayback_max_probes: int = 15
    # Arşivdeki hassas yol türleri (yol üzerinde, büyük/küçük harf duyarsız). Her türün içerik
    # doğrulaması kodda (deep/external/wayback.py); eşleşme tek başına bulgu değildir.
    deep_wayback_patterns: dict[str, str] = Field(
        default_factory=lambda: {
            "env": r"(^|/)\.env([._-][a-z0-9]+)?$",
            "git": r"(^|/)\.git/(head|config)$",
            "sql": r"\.sql(\.gz)?$",
            "backup": r"(backup|yedek|dump|db)[^/]*\.(zip|tar|tgz|tar\.gz|gz|bak|7z|rar)$",
            "config": r"(^|/)(config|settings|credentials|secrets?|appsettings)"
            r"(\.[a-z]+)?\.(json|ya?ml|ini|bak|old|txt)$",
            "phpinfo": r"(^|/)(php)?info\.php$",
            "api_docs": r"(swagger|openapi|api-docs)[^/]*(\.json|\.ya?ml|/?$|\.html$)",
        }
    )
    deep_domain_expiry_high_days: int = 30
    deep_domain_expiry_medium_days: int = 60
    # Sertifika: kısa ömürlü otomatik sertifikalar (Let's Encrypt 90/45/6 gün) yenilemeyi kalan
    # sürenin ~1/3'ünde yapar; eşik ömrün bu oranıyla da sınırlanır (normal yenileme
    # penceresindeki sertifikaya alarm verilmesin).
    deep_cert_expiry_high_days: int = 14
    deep_cert_expiry_medium_days: int = 30
    deep_cert_expiry_high_ratio: float = 0.2
    deep_cert_expiry_medium_ratio: float = 0.3
    deep_cert_timeout_s: float = 10
    # Başarısız istek sayılmayan üçüncü taraf ölçüm/reklam host'ları (kayıtlı alan adı)
    deep_failed_request_ignore_domains: list[str] = Field(
        default_factory=lambda: [
            "google-analytics.com",
            "googletagmanager.com",
            "doubleclick.net",
            "facebook.com",
            "facebook.net",
            "hotjar.com",
            "clarity.ms",
            "yandex.ru",
            "tiktok.com",
            "linkedin.com",
            "sentry.io",
        ]
    )
    # --- Linkler, meta ve yapılandırılmış veri (P6, PHASE2 §6.4; ağ şeridi) ---
    # Bekleme dahil. P11: 120→510 = render beklemesi (420) + link kontrolü (ölçümde render
    # bittikten sonra en fazla ~52 sn; pay ile 90).
    deep_budget_links_s: float = 510
    # Render modülünün linkleri toplaması en fazla bu kadar beklenir (sonra Faz 1 sayfalarıyla).
    # P11: 90→420 (= render bütçesi). 90 sn'de, render'ı uzun süren sitelerde links render'a
    # yetişemiyor ve Faz 1 sayfaları da yoksa (bot engeli) tamamen başarısız oluyordu.
    deep_links_render_wait_s: float = 420
    deep_links_max_internal: int = 100
    deep_links_max_external: int = 40
    deep_links_concurrency: int = 6
    # Aynı host'a iki istek arası en az bu kadar (sn). Hedefin kendisi için daha sık olabilir.
    deep_links_per_host_interval_s: float = 1.0
    deep_links_own_host_interval_s: float = 0.25
    deep_links_request_timeout_s: float = 10
    # Otomatik ziyaretçiyi giriş duvarı / 999 / 429 ile karşılayan siteler: sonuç güvenilmez,
    # bu alan adlarına giden linkler kontrol edilmez (kayıtlı alan adı).
    deep_links_unverifiable_domains: list[str] = Field(
        default_factory=lambda: [
            "linkedin.com",
            "instagram.com",
            "facebook.com",
            "x.com",
            "twitter.com",
            "tiktok.com",
            "threads.net",
            "pinterest.com",
            "wa.me",
            "whatsapp.com",
            "t.me",
        ]
    )
    # Yolunda bu kelimeler geçen iç linklere istek atılmaz (oturum kapatma, sepete ekleme vb.
    # GET ile durum değiştiren uç noktalar; pasiflik ilkesi). Kelime, yol parçalarında aranır.
    deep_links_skip_path_words: list[str] = Field(
        default_factory=lambda: [
            "logout",
            "signout",
            "sign-out",
            "log-out",
            "cikis",
            "oturumu-kapat",
            "unsubscribe",
            "abonelikten-cik",
            "add-to-cart",
            "sepete-ekle",
            "delete",
            "sil",
        ]
    )
    deep_youtube_oembed_endpoint: str = "https://www.youtube.com/oembed"
    deep_youtube_max_videos: int = 15
    # og:image: WhatsApp/Telegram önizlemeleri büyük dosyada görünmeyebilir. Spec eşiği 5 MB.
    deep_og_image_max_bytes: int = 5 * 1024 * 1024
    deep_og_image_min_px: int = 200
    # Genişlik/yükseklik oranı bu aralığın dışındaysa önizlemede kötü kırpılır (önerilen 1.91)
    deep_og_image_ratio_min: float = 1.0
    deep_og_image_ratio_max: float = 2.2
    deep_og_image_max_checks: int = 5
    # --- Gizlilik, tedarik zinciri, erişilebilirlik (P6, PHASE2 §6.5; tarayıcı şeridi) ---
    deep_budget_privacy_s: float = 90
    # Hiçbir şeye tıklamadan bekleme (spec: 5 sn) ve "Reddet"ten sonra
    deep_privacy_observe_s: float = 5
    deep_privacy_after_click_s: float = 2
    # EasyPrivacy (CC BY-SA 3.0; decisions P0) ve retire.js veritabanı (Apache-2.0): imaja
    # gömülür. Dosya yoksa takipçiler sadece elle tutulan listeyle tanınır, açıklı kütüphane
    # check'i atlanır.
    deep_tracker_list_path: str = "/app/data/easyprivacy.txt"
    deep_retire_db_path: str = "/app/data/jsrepository.json"
    # Haftalık güncelleme (worker-deep cron): indirilen kopya buraya yazılır (tmpfs) ve
    # geçerliyse imajdakinin yerine kullanılır.
    deep_retire_update_url: str = (
        "https://raw.githubusercontent.com/RetireJS/retire.js/master/repository/jsrepository.json"
    )
    deep_retire_update_dir: str = Field(default="/tmp/launchcheck-data")  # noqa: S108
    deep_retire_max_bytes: int = 8 * 1024 * 1024
    deep_axe_path: str = "/usr/local/lib/node_modules/axe-core/axe.min.js"
    deep_axe_pages: int = 5  # ana sayfa + 4 kilit sayfa
    deep_axe_timeout_s: float = 25
    # Sürüm tespiti için okunan script gövdeleri
    deep_supply_max_scripts: int = 60
    deep_supply_max_script_bytes: int = 3 * 1024 * 1024
    deep_supply_max_total_bytes: int = 40 * 1024 * 1024
    # Ele geçirilmiş / kötüye kullanılmış script CDN'leri: alan adı → kaynak. Alt alan adları da
    # eşleşir. Yeni giriş eklerken kamuya açık bir olay raporu kaynak olarak yazılmalı.
    deep_risky_script_domains: dict[str, str] = Field(
        default_factory=lambda: {
            "polyfill.io": "Sansec, 2024-06-25: polyfill.io supply chain attack",
            "polyfill.com": "Cloudflare/Censys, 2024-06: polyfill.io sahibine ait alan adları",
            "polyfillcache.com": "Censys, 2024-06: polyfill.io sahibine ait alan adları",
            "bootcdn.net": "Sansec/Censys, 2024-06: polyfill.io ile aynı operatör",
            "bootcss.com": "Sansec/Censys, 2024-06: polyfill.io ile aynı operatör",
            "staticfile.net": "Sansec/Censys, 2024-06: polyfill.io ile aynı operatör",
            "staticfile.org": "Sansec/Censys, 2024-06: polyfill.io ile aynı operatör",
            "rawgit.com": "RawGit 2019'da kapatıldı (terk edilmiş CDN; içerik garanti değil)",
        }
    )
    # --- Huni yürüyüşü (P7, PHASE2 §6.6; tarayıcı şeridi, gizlilikten sonra) ---
    deep_budget_funnel_s: float = 75
    # Ana sayfadan kayıt formuna en fazla tık; ara adımların en uzun yüklenme süresi (ms)
    deep_funnel_max_clicks: int = 3
    deep_funnel_slow_step_ms: int = 3000
    # Kayıt formu: alan ve zorunlu alan sınırları (spec: > 6 alan, > 4 zorunlu alan)
    deep_funnel_max_fields: int = 6
    deep_funnel_max_required: int = 4
    # --- Kıyas (P9, PHASE2 §7; tarayıcı şeridi, huniden sonra) ---
    deep_budget_benchmark_s: float = 120
    # Aynı anda taranan site (kullanıcının sitesi + rakipler)
    deep_benchmark_concurrency: int = 2
    # Referans kütüphanesi yenilemesi (worker-deep saatlik cron): her çalışmada en fazla bu kadar
    # site, aralarında en az bu kadar sn; bir site bu kadar günde bir yeniden taranır.
    deep_reference_sites_per_run: int = 1
    deep_reference_gap_s: float = 30
    deep_reference_refresh_days: int = 7
    # Majestic Million (CC BY 3.0; decisions P0): haftalık popülerlik sıralaması. Boşsa atlanır.
    deep_majestic_url: str = "https://downloads.majestic.com/majestic_million.csv"
    deep_majestic_max_bytes: int = 200 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
