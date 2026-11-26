# Launch Check

Vibe coding ile yapılmış web siteleri ve Android uygulamaları için ücretsiz, yayın öncesi kontrol aracı.
Proje bağlamı ve kurallar: [AGENTS.md](AGENTS.md). Tasarım kararları: [docs/decisions.md](docs/decisions.md).

## Hızlı başlangıç (Docker)

```bash
cp .env.example .env        # isteğe bağlı; yoksa yerel varsayılanlar kullanılır
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

- Web: http://localhost:3000 — API: http://localhost:8000/api/health — Caddy: http://localhost:8080

## Dağıtım (sunucu)

Tek sunucu, Docker Compose, Caddy ile otomatik HTTPS. İki profil var:

| Sunucu | Komut | Not |
|---|---|---|
| 8 vCPU / 16 GB (spec varsayılanı) | `docker compose up -d --build` | P11 ölçümü bu limitlerle yapıldı |
| 2 vCPU / 4 GB (ör. Hetzner CX23) | `docker compose -f docker-compose.yml -f docker-compose.small.yml up -d --build` | Swap şart; derin tarama daha yavaş ve tek sekmeli |

**Önce:** Alan adının A (ve varsa AAAA) kaydı sunucu IP'sini göstermeli. Sunucu güvenlik duvarında 22, 80 ve 443 açık olmalı. Caddy sertifikayı ilk açılışta alır.

```bash
# 1) Docker + git (Ubuntu/Debian; root)
curl -fsSL https://get.docker.com | sh
apt-get install -y git

# 2) Kod
git clone https://github.com/<kullanıcı>/launch-check.git /opt/launch-check
cd /opt/launch-check

# 3) Küçük sunucuda swap (4 GB). Derlemeden ÖNCE: imaj derlemesi de bellek ister
sh deploy/setup-swap.sh

# 4) Ortam: "PROD:" işaretli değerleri doldur
cp .env.example .env
openssl rand -hex 24   # → POSTGRES_PASSWORD (LC_DATABASE_URL içinde de aynı)
openssl rand -hex 32   # → LC_RATE_LIMIT_IP_SALT
nano .env
sh deploy/check-env.sh # yer tutucu kaldıysa durdurur

# 5) Derle ve başlat (ilk derleme 4 GB'lık sunucuda 15-30 dk sürebilir)
docker compose -f docker-compose.yml -f docker-compose.small.yml up -d --build
```

**Doğrulama:**
- `curl https://<alan-adın>/api/health` → `{"status":"ok"}`.
- Sitede kendi sitenle bir hızlı tarama, ardından bir derin tarama yap.
- `docker compose ps` çıktısında tüm servisler `healthy` / `running` olmalı.

**İzleme (ilk günler, özellikle küçük sunucuda):**
- `free -h` ve `docker stats --no-stream`: swap ne kadar kullanılıyor?
- `dmesg | grep -i oom`: bellek yetmezliğinden öldürülen süreç var mı? Varsa derin taramayı kapatma veya sunucuyu büyütme zamanı.
- `docker compose logs --tail 100 worker-deep`.

**Güncelleme:** `git pull` ve aynı `up -d --build` komutu. Veritabanı geçişleri API açılırken otomatik uygulanır (`alembic upgrade head`).

**Notlar:**
- `POSTGRES_PASSWORD` yalnızca veritabanı ilk oluşturulurken uygulanır. Sonradan değiştirmek için Postgres içinde `ALTER USER` gerekir.
- Loglar servis başına 3 × 10 MB ile sınırlı.
- Raporlar ve ekran görüntüleri 7 günde kendiliğinden silinir. Veritabanı yedeği zorunlu değil; asıl korunması gereken şey `.env`.

## Docker olmadan geliştirme

Backend ([apps/api](apps/api)) — [uv](https://docs.astral.sh/uv/) gerekir, Python 3.12'yi kendisi indirir:

```bash
cd apps/api
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run uvicorn launchcheck.main:app --reload      # Postgres + Redis gerekir
uv run arq launchcheck.worker.settings.WorkerSettings
```

Frontend ([apps/web](apps/web)) — Node 22:

```bash
cd apps/web
npm install
npm run dev        # http://localhost:3000 → /tr  (/api istekleri localhost:8000'e yönlenir)
npm run lint && npm run format:check && npm test
```

> **Windows notu:** Docker Desktop, Windows klasör bağlamalarında dosya değişikliği bildirmediği için
> container içindeki `next dev` canlı yenileme yapmaz. Frontend üzerinde çalışırken API/worker/DB'yi
> Docker'da bırakıp `web` servisini durdur ve frontend'i yukarıdaki gibi yerelde `npm run dev` ile çalıştır.
> İletişim linki `NEXT_PUBLIC_CONTACT_URL` ile ayarlanır (build sırasında gömülür).

## APK test fixture'ları

`apps/api/tests/fixtures/apk/*.apk` dosyaları küçük, gerçek ve imzalı APK'lardır. Yeniden üretmek için:

```bash
docker build -t lc-apk-fixtures apps/api/tests/fixtures/apk
docker run --rm -v "$PWD/apps/api/tests/fixtures/apk:/out" lc-apk-fixtures
```

apktool/jadx gerektiren testler (`pytest -m tools`) worker imajında çalışır; CI'daki `apk-tools` job'una bak.

## Credits

Launch Check aşağıdaki açık veri ve araçları kullanır (lisans kararları: `docs/decisions.md`, P0 ve P6/P9):

- [EasyPrivacy](https://easylist.to) takipçi listesi — CC BY-SA 3.0 (sadece eşleştirme verisi olarak, değiştirilmeden)
- [retire.js](https://github.com/RetireJS/retire.js) açıklı kütüphane veritabanı — Apache-2.0
- [axe-core](https://github.com/dequelabs/axe-core) — MPL-2.0 (değiştirilmeden)
- [can-i-take-over-xyz](https://github.com/EdOverflow/can-i-take-over-xyz) — CC BY 4.0
- [Majestic Million](https://majestic.com/reports/majestic-million) — CC BY 3.0
- [Playwright](https://playwright.dev) ve [Lighthouse](https://github.com/GoogleChrome/lighthouse) — Apache-2.0
- [Rare UI](https://rareui.com) arayüz bileşenleri (Swami Malode) — MIT + Commons Clause + Attribution. Kopyalanan dosyalar `apps/web/components/rare/` altında, her birinde telif notu var; lisans metni `apps/web/components/rare/LICENSE`. Sitede alt bilgide "UI: Rare UI" bağlantısı bulunur.
