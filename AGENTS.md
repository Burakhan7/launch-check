# AGENTS.md — Launch Check (Faz 1)

Bu dosya Codex için proje bağlamı ve çalışma talimatıdır. Kod yazmadan önce tamamını oku.

---

## 1. Proje nedir?

**Launch Check**, vibe coding ile (Lovable, Bolt, Replit, Cursor, Claude Code, FlutterFlow, Expo vb.) uygulama yapmış kişilerin, ürünlerini yayına çıkarmadan önce sık yapılan hataları bulmasını sağlayan **ücretsiz bir web aracıdır**.

Kullanıcı:
- Bir **web sitesi URL'si** girer, ya da
- Bir **Android APK** dosyası yükler,

ve 1-2 dakika içinde **puanlı, sade dilde, düzeltme yollarıyla birlikte bir rapor** alır.

### Hedef kitle
Teknik bilgisi sınırlı, ürünü "çalışıyor" ama yayına çıkarken veya sonrasında sorun yaşayan kişiler. Rapor dili teknik jargon değil, **sonuç odaklı** olmalı:
- ❌ "service_role JWT exposed in client bundle"
- ✅ "Sitendeki bu anahtarla herkes tüm kullanıcı verilerini okuyabilir, değiştirebilir ve silebilir."

### Temel ilkeler (ihlal etme)
1. **Yardım odaklı:** Kayıt yok, e-posta kapısı yok. Rapor tamamen ve direkt gösterilir.
2. **Minimum veri:** Yüklenen APK tarama biter bitmez diskten silinir. Raporlar 7 gün sonra otomatik silinir. Kullanıcı kişisel verisi toplanmaz.
3. **Bulunan secret'lar asla tam hâliyle saklanmaz veya gösterilmez.** Sadece maskeli hâli (ilk 6 + son 4 karakter) tutulur.
4. **Faz 1 tamamen pasiftir:** Sadece herkese açık dosyalar okunur. Hedefin backend'ine (Supabase, Firebase, API) yazma/okuma denemesi yapılmaz. Aktif testler Faz 3'tedir ve sahiplik doğrulaması gerektirir.
5. **Her bulgu düzeltilebilir olmalı:** Her bulgu için sade açıklama + neden önemli + düzeltme adımı + AI aracına yapıştırılabilir düzeltme prompt'u.

---

## 2. Yol haritası (bağlam için)

| Faz | Erişim | Kapsam |
|---|---|---|
| **1 (şimdi)** | Sadece dosyalar | Statik analiz: sızan key'ler, açık dosyalar, header'lar, debug build, manifest |
| 2 | Tarayıcı + LLM | Playwright ile davranış, console hataları, PageSpeed, landing/store analizi (Gemini) |
| 3 | Sahiplik onayı | Supabase RLS, Firebase kuralları, korumasız API testleri |
| 4 | Test hesapları | IDOR, yetki yükseltme, paywall bypass, mobil dinamik analiz |

**Sadece Faz 1'i yaz.** Ama mimari Faz 2-4'ü kaldırabilecek şekilde genişletilebilir olmalı (check registry, faz alanı, kategori sistemi).

---

## 3. Teknoloji seçimleri (kesinleşti)

| Katman | Seçim |
|---|---|
| Repo | Monorepo |
| Backend API | Python 3.12, FastAPI, Pydantic v2 |
| Paket yöneticisi (Python) | uv |
| Arka plan işleri | Redis + arq (ayrı worker process, cron job desteği) |
| Veritabanı | PostgreSQL 16, SQLAlchemy 2 (async) + asyncpg, Alembic |
| HTTP istemci | httpx (async) |
| HTML parse | selectolax (veya BeautifulSoup) |
| APK analizi | apktool + jadx (worker Docker imajında, JDK 17+) |
| Frontend | Next.js (App Router, en güncel kararlı sürüm), TypeScript, Tailwind CSS |
| i18n | next-intl, diller: `tr` (varsayılan), `en` |
| Deploy | Hetzner VPS, Docker Compose, Caddy (reverse proxy + otomatik HTTPS) |
| CI | GitHub Actions (lint + test) |
| Test | pytest + pytest-asyncio (backend), Playwright veya Vitest (frontend, minimum) |
| Lint/format | ruff (Python), ESLint + Prettier (TS) |

---

## 4. Monorepo yapısı

```
launch-check/
├── AGENTS.md
├── docker-compose.yml
├── docker-compose.dev.yml
├── Caddyfile
├── .env.example
├── .github/workflows/ci.yml
├── apps/
│   ├── web/                      # Next.js frontend
│   │   ├── app/[locale]/
│   │   │   ├── page.tsx          # Ana sayfa: URL gir / APK yükle
│   │   │   └── report/[id]/page.tsx
│   │   ├── components/
│   │   ├── messages/{tr,en}.json # i18n metinleri
│   │   └── lib/api.ts
│   └── api/                      # Python backend (API + worker aynı paket)
│       ├── pyproject.toml
│       ├── alembic/
│       ├── Dockerfile.api
│       ├── Dockerfile.worker     # JDK + apktool + jadx içerir
│       ├── src/launchcheck/
│       │   ├── main.py           # FastAPI app
│       │   ├── config.py         # pydantic-settings
│       │   ├── api/routes/       # scans.py, health.py
│       │   ├── db/               # models.py, session.py
│       │   ├── worker/           # arq WorkerSettings, tasks.py, cron.py
│       │   ├── security/         # ssrf.py, upload.py, ratelimit.py
│       │   ├── collectors/       # web_collector.py, apk_collector.py
│       │   ├── checks/
│       │   │   ├── base.py       # Check, Finding, Severity, Category
│       │   │   ├── registry.py
│       │   │   ├── shared/       # secret_patterns.py (web + apk ortak)
│       │   │   ├── web/
│       │   │   └── apk/
│       │   ├── scoring.py
│       │   └── i18n/             # bulgu metinleri tr/en (YAML veya JSON)
│       └── tests/
│           ├── fixtures/         # örnek bundle'lar, örnek APK'lar
│           └── checks/
└── docs/
```

---

## 5. Mimari akış

```
[Next.js] --POST /api/scans--> [FastAPI] --enqueue--> [Redis/arq] --> [Worker]
    ^                              |                                    |
    |                          [Postgres] <------ sonuç yazar ----------+
    +---- GET /api/scans/{id} (polling, 2 sn) ----+
```

1. Kullanıcı URL girer veya APK yükler → `POST /api/scans`
2. API girdiyi doğrular (SSRF kontrolü / dosya kontrolü), `scans` tablosuna `queued` kaydı açar, arq'ya job atar, `scan_id` döner
3. Worker:
   - **Collector** çalışır → `ScanContext` üretir (web: HTML, header'lar, JS dosyaları; apk: decompile edilmiş klasör, manifest, sertifika)
   - Registry'deki ilgili tüm **check**'ler `ScanContext` üzerinde çalışır
   - Bulgular `findings` tablosuna, skor `scans` tablosuna yazılır
   - APK ve geçici klasörler **her durumda** (başarı/hata) silinir (`finally`)
4. Frontend `GET /api/scans/{id}` ile polling yapar, `status=done` olunca raporu gösterir
5. **arq cron job** (saatlik): `created_at < now() - 7 gün` olan scan ve finding kayıtlarını siler

---

## 6. Veri modeli

```
scans
  id              UUID PK (tahmin edilemez, rapor linki bu)
  target_type     enum: web | apk
  target_label    text   # web: normalize edilmiş origin (https://example.com), apk: package name. Dosya adı saklanmaz.
  status          enum: queued | running | done | failed
  error_code      text null   # kullanıcıya gösterilecek i18n anahtarı
  score           int null    # 0-100
  category_scores jsonb null  # {"security": 60, "prod_readiness": 85}
  meta            jsonb null  # tespit edilen platform (lovable, supabase, flutter, expo...), apk versiyonu, taranan dosya sayısı
  phase           int default 1
  created_at      timestamptz
  finished_at     timestamptz null

findings
  id              UUID PK
  scan_id         UUID FK -> scans ON DELETE CASCADE
  check_id        text    # ör. "web.secrets.service_role"
  severity        enum: critical | high | medium | low | info
  category        enum: security | prod_readiness | performance | conversion | store_compliance
  evidence        jsonb   # maskeli değer, dosya yolu, satır/offset, header adı vb. ASLA tam secret yok.
  created_at      timestamptz
```

Bulgu başlığı, açıklaması, "neden önemli", düzeltme adımları ve düzeltme prompt'u **DB'de saklanmaz**; `check_id` üzerinden i18n dosyalarından dil bazlı üretilir.

IP adresi veya kullanıcı tanımlayıcı **DB'ye yazılmaz**. Rate limit sadece Redis'te TTL'li sayaçla yapılır.

---

## 7. API

```
POST /api/scans/web
  body: { "url": "https://..." }
  -> 202 { "scan_id": "..." }

POST /api/scans/apk
  multipart: file (.apk), consent=true ("Bu uygulamanın sahibiyim" onayı)
  -> 202 { "scan_id": "..." }

GET /api/scans/{scan_id}?lang=tr|en
  -> { status, score, category_scores, meta, findings: [ {check_id, severity, category,
       title, description, why_it_matters, fix_steps[], fix_prompt, evidence} ] }

GET /api/health
```

Hatalar: tutarlı JSON `{ "error": { "code": "invalid_url", "message": "..." } }`, mesajlar i18n.

---

## 8. Güvenlik (tarayıcının kendisi için — zorunlu)

Bu araç herkesin verdiği URL'ye istek atıyor ve herkesin yüklediği dosyayı açıyor. Bu yüzden:

### SSRF koruması (`security/ssrf.py`)
- Sadece `http`/`https`, sadece 80/443 portları
- DNS çöz, **tüm** çözülen IP'leri kontrol et; private, loopback, link-local, multicast, reserved, CGNAT (100.64.0.0/10), `169.254.169.254` ve IPv6 karşılıkları → reddet
- Her redirect'te aynı kontrolü tekrarla, maksimum 5 redirect
- DNS rebinding'e karşı: çözülen IP'ye bağlan (custom transport) veya bağlantı sonrası peer IP'yi doğrula
- Timeout: bağlantı 5 sn, toplam istek 15 sn, tüm web taraması 90 sn
- Yanıt boyutu limiti: HTML 5 MB, tek JS dosyası 10 MB, toplam 50 MB, en fazla 60 JS dosyası
- User-Agent: `LaunchCheckBot/1.0 (+https://<domain>/bot)`

### APK yükleme (`security/upload.py`)
- Maksimum 150 MB, uzantı + magic bytes (ZIP `PK\x03\x04`) kontrolü
- Zip bomb koruması: toplam açılmış boyut limiti (ör. 1 GB), dosya sayısı limiti, path traversal kontrolü (`../`)
- Rastgele adla geçici dizine kaydet, `finally` bloğunda sil
- apktool/jadx: `subprocess` ile, timeout (apktool 120 sn, jadx 240 sn), shell=False, düşük yetkili kullanıcı, sadece geçici dizin
- `consent=true` gelmezse reddet

### Rate limit (`security/ratelimit.py`)
- Redis, IP başına: 10 web taraması / saat, 3 APK taraması / saat (config'ten)
- Anahtarlar TTL'li, IP hash'lenerek tutulur

### Secret işleme
- Bulunan secret'lar bellekte maskelenir, `evidence`'a sadece maskeli hâli yazılır
- Loglara asla tam secret, tam HTML veya APK içeriği yazılmaz

---

## 9. Check sistemi

```python
class Severity(StrEnum): CRITICAL, HIGH, MEDIUM, LOW, INFO
class Category(StrEnum): SECURITY, PROD_READINESS, PERFORMANCE, CONVERSION, STORE_COMPLIANCE

@dataclass
class Finding:
    check_id: str
    severity: Severity
    category: Category
    evidence: dict

class Check(ABC):
    id: str               # "web.files.env_exposed"
    target: Literal["web", "apk"]
    phase: int = 1
    category: Category
    default_severity: Severity
    async def run(self, ctx: ScanContext) -> list[Finding]: ...
```

- Her check **bağımsız** ve **tek sorumluluklu** olsun
- Bir check exception atarsa tarama durmasın; hata loglansın, o check atlanmış sayılsın
- Registry `@register` decorator ile otomatik toplasın
- Her check için en az bir pozitif ve bir negatif test (fixture ile) zorunlu
- Her `check_id` için `i18n/tr.yaml` ve `i18n/en.yaml` içinde şu alanlar zorunlu: `title`, `description`, `why_it_matters`, `fix_steps` (liste), `fix_prompt`. Eksikse test başarısız olsun.

### Ortak: secret desenleri (`checks/shared/secret_patterns.py`)

Web bundle'ları ve APK string'leri aynı desen kütüphanesini kullanır.

| id | Desen (başlangıç) | Önem | Not |
|---|---|---|---|
| openai | `sk-(proj-\|svcacct-\|admin-)?[A-Za-z0-9_-]{20,}` | critical | |
| anthropic | `sk-ant-[a-z0-9]+-[A-Za-z0-9_-]{80,}` | critical | |
| openrouter | `sk-or-v1-[a-f0-9]{64}` | critical | |
| groq | `gsk_[A-Za-z0-9]{40,}` | critical | |
| stripe_live_secret | `(sk\|rk)_live_[0-9a-zA-Z]{20,}` | critical | |
| stripe_test_secret | `sk_test_[0-9a-zA-Z]{20,}` | medium | |
| supabase_secret | `sb_secret_[A-Za-z0-9_-]{20,}` | critical | yeni Supabase key formatı |
| supabase_jwt | `eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` | — | payload decode et: `role == "service_role"` → critical, `anon` → info (normal) |
| aws_access_key | `AKIA[0-9A-Z]{16}` | critical | |
| github_token | `gh[pousr]_[A-Za-z0-9]{36,}` | critical | |
| sendgrid | `SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}` | high | |
| resend | `re_[A-Za-z0-9]{8,}_[A-Za-z0-9]{16,}` | high | false positive riski, test et |
| twilio | `SK[0-9a-fA-F]{32}` | high | |
| replicate | `r8_[A-Za-z0-9]{30,}` | high | |
| private_key | `-----BEGIN (RSA \|EC \|OPENSSH )?PRIVATE KEY-----` | critical | |
| google_api_key | `AIza[0-9A-Za-z_-]{35}` | medium | Firebase config key'leri public'tir, ama aynı format Gemini key'i olabilir. Başlık: "Google API anahtarı bulundu, kısıtlamalarını kontrol et". Critical deme. |

Kurallar:
- Desenler tek bir yerde, kolay genişletilebilir
- Entropi kontrolü ve bilinen placeholder'ları eleme (`sk-xxxx`, `your-api-key`, `sk_test_123`) false positive'i azaltmak için
- Aynı secret birden fazla dosyada çıkarsa tek bulgu, `evidence.locations` listesi

---

## 10. Faz 1 — Web check'leri

### Collector (`collectors/web_collector.py`)
- URL'yi normalize et, SSRF kontrolü, ana sayfayı çek (header'lar + HTML)
- HTML'den `<script src>`, `<link rel=modulepreload>` ile aynı origin JS dosyalarını topla
- Çekilen JS içinde `/assets/...js`, `/_next/static/...js` gibi lazy chunk yollarını regex ile bul, limitler dahilinde onları da çek
- Platform tespiti → `meta.platform`: Lovable, Bolt, Next.js, Vite, Supabase (`*.supabase.co`), Firebase (`firebaseapp.com`, `apiKey` + `projectId`), Vercel, Netlify (header'lardan)

### Check listesi

| check_id | Ne yapar | Önem |
|---|---|---|
| `web.secrets.leaked_key` | JS bundle + inline script'lerde secret desenleri | critical/high/medium (desene göre) |
| `web.secrets.service_role` | Supabase JWT decode, `service_role` | critical |
| `web.files.env_exposed` | `/.env`, `/.env.local`, `/.env.production`, `/.env.development` | critical |
| `web.files.git_exposed` | `/.git/config`, `/.git/HEAD` | critical |
| `web.files.backup_exposed` | `/backup.sql`, `/db.sql`, `/dump.sql`, `/backup.zip`, `/config.json` | high |
| `web.files.sourcemap_exposed` | Çekilen JS'lerin `.map` dosyası erişilebilir mi / `sourceMappingURL` | high |
| `web.bundle.localhost_urls` | Bundle'da `localhost`, `127.0.0.1`, `0.0.0.0`, `10.0.2.2` API adresleri | high |
| `web.bundle.staging_urls` | `staging.`, `dev.`, `-dev.`, `test.` içeren API URL'leri | medium |
| `web.seo.noindex_left` | `<meta name=robots content=noindex>` veya `X-Robots-Tag: noindex` | high |
| `web.seo.robots_disallow_all` | `robots.txt` içinde `Disallow: /` (User-agent: *) | high |
| `web.https.missing` | HTTPS yok veya HTTP→HTTPS yönlendirmesi yok | high |
| `web.https.mixed_content` | HTTPS sayfada `http://` script/stylesheet | medium |
| `web.headers.hsts_missing` | | medium |
| `web.headers.csp_missing` | | low |
| `web.headers.frame_options_missing` | X-Frame-Options veya CSP `frame-ancestors` yok | low |
| `web.headers.nosniff_missing` | | low |
| `web.headers.server_version_leak` | `Server`/`X-Powered-By` versiyon bilgisi | low |
| `web.legal.privacy_missing` | Gizlilik politikası / KVKK aydınlatma metni linki yok (TR+EN anahtar kelimeler) | medium |
| `web.legal.terms_missing` | Kullanım şartları linki yok | low |
| `web.meta.title_description_missing` | `<title>`, meta description | low |
| `web.meta.og_missing` | OG title/description/image | low |
| `web.meta.favicon_missing` | | low |
| `web.meta.sitemap_missing` | `/sitemap.xml` | low |

Dosya erişim kontrollerinde **SPA catch-all false positive'ine dikkat:** Birçok SPA her yola `200` + `index.html` döner. Bir dosyayı "açık" saymadan önce:
- İçerik tipi ve içerik doğrulaması yap (`.env` için `KEY=VALUE` satırları, `.git/config` için `[core]`)
- Rastgele var olmayan bir yolun (`/<uuid>.txt`) yanıtıyla karşılaştır

---

## 11. Faz 1 — APK check'leri

### Collector (`collectors/apk_collector.py`)
- `apktool d` → manifest (decode edilmiş XML), resources, smali, assets
- `jadx` → Java kaynakları (sadece string tarama için; timeout'ta jadx olmadan devam et)
- `keytool -printcert -jarfile` → imza sertifikası
- Framework tespiti → `meta.framework`:
  - Flutter: `lib/*/libflutter.so`
  - React Native: `assets/index.android.bundle` (Hermes ise bytecode, string çıkarımı yap)
  - Expo: `expo` paket/sınıf adları
  - FlutterFlow: Flutter + FlutterFlow'a özgü asset/paket izleri
  - Native/Kotlin: yukarıdakilerin hiçbiri
- String kaynakları: smali `const-string`, jadx çıktısı, `res/values/strings.xml`, RN bundle, Flutter `libapp.so` üzerinde `strings` benzeri (printable ASCII ≥ 8 karakter)
- `google-services.json` değerleri (`res/values/strings.xml` içindeki `google_api_key`, `project_id`, `firebase_database_url`) ve Supabase URL'leri → `meta` (Faz 3 hazırlığı, Faz 1'de test edilmez)

### Check listesi

| check_id | Ne yapar | Önem |
|---|---|---|
| `apk.secrets.leaked_key` | Tüm string kaynaklarında secret desenleri | critical/high/medium |
| `apk.secrets.service_role` | Supabase JWT decode, `service_role` | critical |
| `apk.build.debuggable` | Manifest `android:debuggable="true"` | critical |
| `apk.build.debug_certificate` | Sertifika `CN=Android Debug` | critical |
| `apk.build.flutter_debug` | `assets/flutter_assets/kernel_blob.bin` var | critical |
| `apk.build.rn_dev_mode` | RN bundle yok/dev server'a bağlanıyor, Expo dev client sınıfları | high |
| `apk.network.localhost_urls` | `localhost`, `127.0.0.1`, `10.0.2.2`, LAN IP'leri (`192.168.`, `10.`) API olarak | high |
| `apk.network.staging_urls` | staging/dev API URL'leri | medium |
| `apk.network.cleartext` | `usesCleartextTraffic="true"` veya network security config'de cleartext izni, ya da `http://` API URL'leri | high |
| `apk.sdk.target_too_low` | `targetSdkVersion` < config'teki `PLAY_MIN_TARGET_SDK` | high |
| `apk.manifest.allow_backup` | `android:allowBackup="true"` (veya varsayılan) | medium |
| `apk.manifest.exported_components` | `intent-filter`'sız ama `exported="true"` activity/service/receiver/provider | medium |
| `apk.permissions.sensitive` | SMS, rehber, arama kaydı, arka plan konumu, `MANAGE_EXTERNAL_STORAGE`, `QUERY_ALL_PACKAGES` gibi Play'in kısıtladığı izinler | medium |
| `apk.logging.sensitive_log` | `Log.d`/`print`/`console.log` çağrılarına yakın `token`, `password`, `secret` string'leri (sezgisel, düşük güven) | low |

`PLAY_MIN_TARGET_SDK` config'ten gelir. **Değeri koda gömme;** Google'ın güncel gereksinimini developer.android.com'dan doğrula ve `.env.example`'da yorumla belirt.

---

## 12. Skorlama (`scoring.py`)

- Her kategori 100'den başlar
- Ceza: critical −30, high −12, medium −5, low −2, info 0
- Aynı check'ten gelen birden fazla bulgu: ilki tam ceza, sonrakiler yarım, bir check'in toplam cezası en fazla 2× tam ceza
- Kategori skoru en az 0
- Genel skor: kategori skorlarının ağırlıklı ortalaması (Faz 1: security 0.7, prod_readiness 0.3)
- **En az bir critical varsa genel skor en fazla 49** ("yayına hazır değil" eşiği)
- Etiketler: 0-49 "Yayına hazır değil", 50-79 "Düzeltilmesi gerekenler var", 80-100 "İyi durumda"

---

## 13. Rapor metinleri (i18n)

Her bulgu için `tr` ve `en`:

```yaml
web.secrets.service_role:
  title: "Supabase yönetici anahtarın herkese açık"
  description: "Sitenin kodunda Supabase'in service_role anahtarı bulundu. Bu anahtar tüm güvenlik kurallarını atlar."
  why_it_matters: "Bu anahtarı bulan herkes tüm kullanıcı verilerini okuyabilir, değiştirebilir ve silebilir. Siteni ziyaret eden herkes bu anahtarı görebilir."
  fix_steps:
    - "Supabase panelinden bu anahtarı hemen yenile (Project Settings → API)."
    - "Frontend kodunda sadece anon/publishable anahtarı kullan."
    - "Yönetici yetkisi gereken işlemleri Edge Function veya backend'e taşı."
  fix_prompt: |
    Projemde Supabase service_role anahtarı frontend koduna sızmış. Lütfen:
    1. Frontend'deki tüm service_role kullanımlarını bul ve kaldır.
    2. Frontend'de sadece anon key kullanılsın.
    3. service_role gerektiren işlemleri bir Supabase Edge Function'a taşı ve frontend'den onu çağır.
    4. İlgili tablolarda RLS'in açık olduğunu ve doğru policy'ler olduğunu kontrol et.
```

Ton: Sade, suçlamayan, net. Teknik terim gerekiyorsa parantez içinde açıkla.

---

## 14. Frontend (Faz 1)

Sayfalar:
- **Ana sayfa (`/[locale]`)**: Kısa başlık + iki sekme: "Web sitesi" (URL kutusu) / "Android uygulaması" (APK sürükle-bırak + "Bu uygulamanın sahibiyim" onay kutusu). Altında: neleri kontrol ettiğimiz, "Dosyanı saklamıyoruz, raporlar 7 gün sonra silinir" notu.
- **Tarama ekranı**: Polling, adım adım ilerleme ("Dosyalar toplanıyor", "Anahtarlar aranıyor"...)
- **Rapor sayfası (`/[locale]/report/[id]`)**:
  - Genel skor + etiket + kategori skorları
  - Tespit edilen platform/framework rozetleri
  - Bulgular önem sırasına göre, her biri açılır kart: açıklama, neden önemli, düzeltme adımları, **"Prompt'u kopyala"** butonu, kanıt (maskeli)
  - Hiç bulgu olmayan kategoriler için "geçti" listesi (neyi kontrol ettiğimizi göstermek güven verir)
  - Paylaş linki, "Düzelttin mi? Tekrar tara" butonu
  - Sayfa sonunda küçük, rahatsız etmeyen not: "Kendin çözemediysen yardım alabilirsin" + iletişim linki
- **Gizlilik sayfası**: Ne saklanıyor, ne kadar süre, ne saklanmıyor

Tasarım: Sade, mobil uyumlu, karanlık mod desteği. Rapor sayfası `noindex` (kullanıcı raporları Google'da çıkmamalı), ana sayfa SEO'ya açık.

---

## 15. Docker Compose servisleri

- `web` — Next.js (production build)
- `api` — FastAPI (uvicorn)
- `worker` — arq worker + cron (JDK 17, apktool, jadx içeren imaj)
- `postgres` — volume ile
- `redis`
- `caddy` — `/api/*` → api, diğer her şey → web, otomatik HTTPS

`docker-compose.dev.yml`: hot reload, açık portlar.

Worker container'ı: read-only root filesystem (geçici dizin hariç), CPU/memory limitleri, ağ çıkışı sadece web taraması için gerekli.

---

## 16. Geliştirme sırası (milestone'lar)

Her milestone sonunda testler geçmeli, `docker compose up` çalışmalı.

- **M0 — İskelet:** Monorepo, Docker Compose, FastAPI health, Next.js boş sayfa (tr/en), Postgres + Alembic ilk migration, Redis + arq worker "hello" job, CI (ruff, pytest, eslint)
- **M1 — Çekirdek:** `Check`/`Finding`/registry, `ScanContext`, scoring, i18n yükleyici + eksik metin testi, scans/findings modelleri, `POST`/`GET` scan endpoint'leri, cron silme job'u
- **M2 — Web güvenliği:** SSRF modülü (kapsamlı testlerle), rate limit, web collector, platform tespiti
- **M3 — Web check'leri:** Bölüm 10'daki tüm check'ler + fixture testleri
- **M4 — APK altyapısı:** Upload güvenliği, worker imajı (apktool/jadx/keytool), APK collector, framework tespiti, garantili temizlik
- **M5 — APK check'leri:** Bölüm 11'deki tüm check'ler + fixture testleri (fixture için küçük örnek APK'lar: debug Flutter, release Flutter, RN, native)
- **M6 — Frontend:** Ana sayfa, tarama ekranı, rapor sayfası, prompt kopyalama, gizlilik sayfası
- **M7 — Prod:** Caddy, prod compose, kaynak limitleri, loglama, README (kurulum + deploy)

---

## 17. Kod kuralları

- Python: tip ipuçları zorunlu, `ruff` temiz, async öncelikli, fonksiyonlar küçük
- Secret/HTML/APK içeriği loglanmaz
- Konfigürasyon sadece `config.py` (pydantic-settings) üzerinden, sihirli sayılar yok
- Her yeni check: implementasyon + i18n (tr/en) + pozitif/negatif test = tamamlanmış sayılır
- Dış ağa çıkan testler yok; tüm testler fixture veya mock (respx) ile
- Commit'ler küçük ve açıklayıcı
- Emin olmadığın bir tasarım kararında varsayım yapma, `docs/decisions.md`'ye not düş ve en basit güvenli seçeneği uygula

---

## 18. Faz 1 için "bitti" tanımı

- [ ] Bir web URL'si ve bir APK, uçtan uca taranıp rapor sayfasında TR ve EN olarak görüntülenebiliyor
- [ ] Bölüm 10 ve 11'deki tüm check'ler testleriyle mevcut
- [ ] SSRF testleri: private IP, redirect ile private IP, IPv6 loopback, metadata IP reddediliyor
- [ ] APK dosyası ve geçici klasörler tarama sonrası (hata durumunda dahil) diskte yok
- [ ] Hiçbir yerde (DB, log) tam secret yok
- [ ] 7 günden eski kayıtlar cron ile siliniyor
- [ ] `docker compose up` ile tüm sistem tek komutla ayağa kalkıyor
