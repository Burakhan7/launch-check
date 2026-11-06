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
        default_factory=lambda: {"security": 0.7, "prod_readiness": 0.3}
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
