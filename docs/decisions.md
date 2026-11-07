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

## 2026-11-05 — M2

- **SSRF, bağlantı katmanında:** httpx 0.28 `network_backend` parametresi sunmadığı için `GuardedTransport`, httpx transport'unun iç `_pool`'unu `GuardedNetworkBackend` kullanan bir `httpcore.AsyncConnectionPool` ile değiştirir. Bu backend her TCP bağlantısında host'u yeniden çözer, tüm IP'leri doğrular, doğrulanan IP'ye bağlanır ve bağlandıktan sonra peer IP'yi tekrar kontrol eder. TLS SNI/sertifika doğrulaması orijinal host adıyla yapılır. `test_transport_uses_guarded_backend` bu iç yapı varsayımını doğrular; httpx yükseltilirse bu test kırılır.
- **Eski IPv4 yazımları** (`2130706433`, `0x7f000001`, `127.1`, `0177.0.0.1`) işletim sistemine bırakılmaz; `inet_aton` kurallarıyla IP'ye çevrilip literal IP gibi kontrol edilir. Windows bunları hiç çözmüyor, Linux 127.0.0.1'e çeviriyor; davranış platformdan bağımsız olmalı.
- **NAT64 (`64:ff9b::/96`) engelli:** Python bu aralığı reserved sayıyor. Sunucu DNS64 kullanmadığı için normal bir sitede karşılaşılmaz.
- **API'de DNS ön kontrolü:** `POST /api/scans/web` özel/çözülemeyen adresleri kuyruğa almadan reddeder (hızlı geri bildirim). Asıl koruma worker'daki bağlantı katmanıdır.
- **Accept header'ı tarayıcı benzeri:** `*/*` gönderildiğinde bazı siteler (ör. nextjs.org) HTML yerine markdown döndürüyor. Kullanıcının gördüğü sayfayı taramak için `text/html,...;q=0.9,*/*;q=0.8` gönderilir.
- **Sadece aynı origin JS (AGENTS.md §10):** Script'lerini alt alan adı CDN'inden yükleyen siteler (ör. supabase.com → `frontend-assets.supabase.com`) için JS taraması 0 dosya bulur. Lovable/Bolt/Vite uygulamalarında script'ler aynı origin'den gelir. *Açık soru:* "aynı site" (aynı kayıtlı alan adının alt alan adları) kapsama alınsın mı? Doğru yapmak için Public Suffix List gerekir.
- **Rate limit Redis'e erişemezse açık kalır (fail-open):** Redis yoksa kuyruk da çalışmaz ve istek zaten 503 ile döner; bu durumda kullanıcıyı ayrıca engellemenin anlamı yok.
- **İstemci IP'si:** Caddy arkasında uvicorn `--proxy-headers` ile `X-Forwarded-For`'dan okunur. Prod'da api portu dışarı açık değil, sadece Caddy erişir. Dev compose'da 8000 portu açık olduğu için header taklit edilebilir; bu sadece dev'de geçerli.
- **selectolax 1.0:** Eski `modest` motoru kaldırıldı, `lexbor` (`LexborHTMLParser`) kullanılıyor.

## 2026-11-05 — M3

- **`web.secrets.google_api_key` ayrı check:** AGENTS.md google_api_key için özel bir başlık istiyor ("kısıtlamalarını kontrol et"). Başlıklar check_id başına tanımlandığı için Google anahtarları `web.secrets.leaked_key` yerine kendi check'inde (medium) raporlanır. APK tarafında da `apk.secrets.google_api_key` aynı mantıkla eklenecek.
- **Supabase anon key bulgu değildir:** `role=anon` JWT'ler normaldir ve raporlanmaz. Sadece `service_role` raporlanır (critical).
- **OpenAI deseni ek filtre:** `sk-` öneki genel olduğu için tireli kelimeler (ör. `sk-modal-overlay-container`) entropi eşiğini geçebiliyor. OpenAI eşleşmelerinde büyük harf + küçük harf + rakam karışımı zorunlu.
- **Ana sayfa taranamıyorsa rapor yok:** Ana sayfa 4xx/5xx dönerse (`site_http_error`) veya bot doğrulama sayfası gelirse (`site_blocked_bot`: Cloudflare, Vercel Security Checkpoint, DataDome, Imperva) tarama başarısız sayılır. Gerçek denemede lovable.dev Cloudflare doğrulama sayfası döndürdü, nextjs.org ise art arda taramalardan sonra Vercel checkpoint'i gösterdi; doğrulama sayfasını taramak yanıltıcı bir rapor üretir (ör. sahte noindex bulgusu).
- **Localhost check'i:** Çıplak `http://localhost` sayılmaz (kütüphanelerde sık geçer); port veya yol olmalı. OpenTelemetry OTLP varsayılanları (`localhost:4317/4318`) yok sayılır (nextjs.org'da gerçek yanlış alarm).
- **Staging check'i:** İlk etiket kuralı (`dev.`, `staging.`) sadece en az 3 parçalı host'lara uygulanır (`staging.com` alan adının kendisidir). `.test/.local/.internal` uzantıları da geliştirme adresi sayılır.
- **Yasal sayfalar (SPA):** Ham HTML'de link yoksa JS bundle'larında rota string'i aranır (`"/privacy"`, `"/kvkk"`). Bu yaklaşım yanlış alarmdan kaçınmak için hoşgörülüdür; Faz 2'de tarayıcıda render edilip kesinleştirilecek.
- **Source map:** Map dosyaları genelde probe limitinden (1 MB) büyük olduğu için tam JSON yerine başlangıç imzasına bakılır.
- **`.env` kanıtı:** Sadece değişken adları (en fazla 10) saklanır, değerler asla saklanmaz.
- **i18n dosya düzeni:** Bulgu metinleri `i18n/checks/<grup>.<dil>.yaml` dosyalarında; loader hepsini birleştirir ve aynı check_id iki dosyada tanımlıysa hata verir.

## 2026-11-05 — M4

- **İmza sertifikası saf Python ile okunur, keytool kullanılmaz:** `keytool -printcert -jarfile` sadece v1 (JAR) imzasını okur. minSdk >= 24 olan modern uygulamalar genelde sadece v2/v3 ile imzalanır. Bu yüzden v1 (META-INF/*.RSA) ve APK Signing Block (v2/v3/v3.1) doğrudan okunur ve DER içinde CN (OID 2.5.4.3) aranır. Amaç sadece `CN=Android Debug` tespiti olduğu için tam X.509 ayrıştırması gerekmez.
- **Assets apktool'a açtırılmaz (`--no-assets`):** RN bundle, flutter_assets ve paketlenmiş `.env` gibi dosyalar doğrudan ZIP'ten, dosya başına boyut limitiyle (`apk_max_asset_bytes`) okunur. Daha hızlı ve diske daha az yazar.
- **jadx kullanılmayan sabitleri siler:** Fixture'da görüldü. Bu yüzden secret taramasında asıl kaynak smali `const-string` satırları (satır numaraları korunarak); jadx çıktısı ek kaynak olarak ve loglama sezgisi için kullanılır. jadx hata veya zaman aşımıyla biterse onsuz devam edilir.
- **jadx `HOME` değil Java `user.home`'u kullanır:** Salt okunur container'da `/home/app` yazılamadığı için jadx çöküyordu (sadece prod ayarlarıyla görülen bir hata). Araçlar `-Duser.home`, `JADX_CONFIG_DIR` ve `JADX_CACHE_DIR` ile geçici çalışma klasörüne yönlendirilir.
- **Ortak yükleme volume'u:** API ve worker ayrı container'lar; APK `uploads` volume'u üzerinden aktarılır. Worker'a job argümanı olarak sadece rastgele dosya ADI (`<32 hex>.apk`) gider ve worker bu adı kendi yükleme klasörü içinde doğrular (yol enjeksiyonu yok). İki container aynı uid (10001) ile çalışır.
- **Silme garantisi, dört katman:** (1) collect hata/zaman aşımı/iptalde kendi temizliğini yapar, (2) başarıda `ApkContext.aclose()`, (3) `run_scan` finally bloğu (collector'a hiç ulaşılmasa bile), (4) cron 1 saatten eski yetim yüklemeleri ve geçici klasörleri siler. API tarafında doğrulama veya kuyruk hatasında dosya hemen silinir.
- **Zip bomb:** Bildirilen (central directory) boyutlar, girdi sayısı ve 1 MB üstü girdilerde sıkıştırma oranı (>200) kontrol edilir. Bildirilen boyutlar yalan olabilir; son savunma hattı worker'daki 2 GB'lık tmpfs ve araç zaman aşımlarıdır.
- **Test fixture'ları:** Android SDK olmadan `apktool b` (aapt2 + smali) ile derlenip JDK `jarsigner` ile imzalanır (`tests/fixtures/apk/build_fixtures.py`). Sahte anahtarlar betikte üretilir, repoya düz metin olarak girmez. apktool/jadx gerektiren testler `@pytest.mark.tools` ile işaretlidir ve CI'da worker imajında çalışır.
- **Framework önceliği:** flutterflow > flutter > expo > react_native > native. FlutterFlow tespiti `libapp.so` içindeki `flutter_flow/` Dart paket yollarına dayanır.
- **Firebase değerleri meta'ya maskeli girer:** `google_api_key` maskelenir; `project_id` ve `firebase_database_url` olduğu gibi tutulur (Faz 3 hazırlığı, Faz 1'de bunlara istek atılmaz).

## 2026-11-06 — M5

- **`apk.secrets.google_api_key` ayrı check (medium):** Web'deki gibi. Firebase kullanan her uygulamada `google-services.json` anahtarı bulunur; bu yüzden metin "kısıtlamalarını kontrol et" diyor, kritik demiyor.
- **Kategori ataması (Faz 1 ağırlıkları sadece security/prod_readiness):** Play'in reddetme sebepleri (debug sertifikası, düşük target SDK, hassas izinler) ve debug build'ler `prod_readiness`'ta. Debuggable, cleartext, allowBackup, exported bileşenler, loglama ve secret'lar `security`'de. `store_compliance` kategorisi Faz 2'de ağırlık alınca bu check'lerin bir kısmı oraya taşınabilir.
- **RN dev mode Metro adreslerine bakmaz:** RN'nin kendi kodu release build'de de `localhost:8081` / `10.0.2.2` içerir. Sinyaller: APK'da `assets/index.android.bundle` yoksa veya Expo dev client paketleri (`expo/modules/devlauncher`, `devmenu`) varsa. Aynı sebeple RN/Expo uygulamalarında localhost check'i 8081/8097/19000-19002 portlarını yok sayar; native uygulamalarda bu portlar yine raporlanır.
- **LAN IP'leri (192.168/10/172.16-31) APK localhost check'ine dahil:** Mobil geliştiriciler telefondan bilgisayarlarına bu adreslerle bağlanır. Web check'inde LAN IP'leri yok (web'de nadiren ve farklı anlamda görülür).
- **Cleartext:** Manifest `usesCleartextTraffic="true"`, targetSdk < 28 ve hiçbir ayar yoksa varsayılan (açık), network security config'de `base-config` veya localhost olmayan `domain-config` izinleri. Kütüphanelerde çok sayıda `http://` namespace URI'si (`schemas.android.com`, `w3.org`) olduğu için http URL'leri sadece API'ye benziyorsa (`api.` host'u veya `/api`, `/v1`, `/graphql`, `/rest` yolu) sayılır.
- **allowBackup:** `dataExtractionRules` veya `fullBackupContent` tanımlıysa bulgu üretilmez (geliştirici yedeklemeyi bilinçli olarak yapılandırmış).
- **Exported bileşenler:** `android:permission` ile korunanlar hariç tutulur.
- **Hassas izinler listesi:** SMS/MMS/WAP, arama kaydı, rehber, arka planda konum, `MANAGE_EXTERNAL_STORAGE`, `QUERY_ALL_PACKAGES`, `REQUEST_INSTALL_PACKAGES`, `READ_MEDIA_IMAGES/VIDEO`, `USE_FULL_SCREEN_INTENT`. Liste `checks/apk/manifest.py` içinde ve Play politikaları değiştikçe güncellenmeli.
- **Loglama sezgisi sadece uygulamanın kendi paketinde:** jadx çıktısında sadece manifest `package` yolundaki dosyalara bakılır. Üçüncü taraf SDK'lar ("Token refresh" gibi mesajlar) yanlış alarm üretir. Dart/Hermes kodunda çağrı bilgisi olmadığı için Flutter/RN uygulamalarında bu check pratikte sadece native kodu kapsar.
- **Fixture matrisi:** `tests/checks/test_apk_fixture_matrix.py` her fixture'da çıkan bulgu kümesinin TAM olarak beklenenle eşleşmesini ister (eksik = kaçırılan sorun, fazla = yanlış alarm).

## 2026-11-06 — M6

- **Tema:** Mor + beyaz (kullanıcı isteği). Renkler `globals.css` içinde CSS değişkeni olarak tanımlı (`--brand`, `--surface`, `--line`, önem ve skor renkleri). Karanlık mod işletim sistemi tercihini izler (koyu mor zemin, açık mor vurgu). Önem dereceleri ve skor etiketleri anlamlarını korumak için kırmızı/turuncu/amber/yeşil kalır; "low" (düşük) önem markaya uyumlu olarak mor.
- **Rapor sayfası istemcide yüklenir:** `cacheComponents` ile sayfa kabuğu önceden üretilir; rapor `useParams` + 2 sn'lik polling ile istemcide gelir (`<Suspense>` içinde). Sayfa gizlenince (Next 16 `<Activity>`) effect temizlenir ve polling durur. Rapor sayfası `noindex, nofollow` ve `referrer: no-referrer` (rapor linki dış sitelere Referer ile sızmasın).
- **İlerleme adımları tahminidir:** API gerçek adım bilgisi vermediği için adımlar tipik süreye göre ilerler (web ~35 sn, APK ~60 sn) ve son adım tarama bitene kadar bekler. Faz 2'de worker'dan gerçek ilerleme alınabilir.
- **APK yükleme `XMLHttpRequest` ile:** fetch yükleme ilerlemesi vermiyor; ilerleme çubuğu için XHR kullanılır.
- **Dil değiştirici:** Dinamik sayfalarda (rapor) mevcut adres önceden üretilemediği için `<Suspense>` içindedir; yedek hâli diğer dilin ana sayfasına gider.
- **`/api` rewrite'ı:** Geliştirmede Next.js `/api/*` isteklerini `API_INTERNAL_URL`'e (varsayılan localhost:8000) yönlendirir. Prod'da Caddy `/api/*`'yi doğrudan API'ye gönderir; rewrite'a ulaşılmaz.
- **İletişim adresi yer tutucu:** `NEXT_PUBLIC_CONTACT_URL` (varsayılan `mailto:merhaba@launchcheck.example`). Alan adı belli olunca (M7) güncellenecek.
- **Frontend testleri:** Vitest ile saf yardımcılar (kanıt biçimlendirme, API hata ayrıştırma). Vitest 5, `@types/node` >= 22 istediği için tipler Node 22'ye çekildi (çalışma ortamı zaten Node 22).

## 2026-11-06 — İç sayfa taraması, origin_exposed, empty_server_html, og_missing v2

- **İç sayfa taraması:** Ana sayfadaki aynı origin linklerden en fazla `LC_WEB_MAX_INTERNAL_PAGES` (10) sayfa çekilir. Öncelik nav > header > footer > diğer linkler. Query/fragment atılır, dosya uzantılı ve mailto/tel/javascript linkleri elenir, `/x` ve `/x/` tekilleştirilir. 200 olmayan, `text/html` olmayan veya başka origin'e yönlenen sayfalar atlanır. Sayfa JS'leri ayrıca çekilmez (bundle'lar genelde ortaktır; limitler korunur).
- **Süre:** Pipeline'ın 90 sn timeout'u dolarsa tüm tarama başarısız olur. Bu yüzden iç sayfalar kendi süre sınırıyla (`LC_WEB_INTERNAL_PAGES_TIMEOUT_S`, 30 sn) çekilir; dolunca bekleyen istekler iptal edilir ve elde olanlarla devam edilir.
- **Dinamik bulgu metinleri:** `Check.text_params(evidence, fragment)` kanıttan metin parametreleri üretir; i18n metinlerindeki `{yer_tutucu}`lar rapor üretilirken doldurulur. Dile özgü parçalar check'in i18n `fragments` alanındadır. Şablonlu başlıklar "geçti" listesinde anlamsız olacağı için bu check'ler `passed_title` tanımlar (testle zorunlu). Parametre üretimi hata verirse rapor bozulmaz, şablon olduğu gibi kalır.
- **og:url eksik sayılmaz:** Spec og:url'in de kontrol edilmesini istiyor, ama aynı spec "hiç OG yok → üçü de raporlanıyor" ve metalgym senaryosunda "sadece og:image" diyor. og:url yoksa paylaşım platformları sayfanın adresini kullanır; bu yüzden eksik listesine girmez.
- **og: etiketleri `name` ile de okunur:** Eski check sadece `property="og:..."` okuyordu; `name="og:..."` yazan siteler için mevcut etiketler yok sayılıp "üçü de eksik" raporlanabiliyordu.
- **og önem kuralı:** Ana sayfada og:image eksikken diğer etiketlerden en az biri varsa → medium. Diğer her durum (iç sayfalar, tüm etiketlerin eksik olması, göreli görsel, en-boy oranı notu) → low (mevcut önem).
- **origin_exposed:** Platform listesi `LC_WEB_PLATFORM_DEFAULT_DOMAINS` (config). Sadece alt alan adları eşleşir (`*.vercel.app`); çıplak `vercel.app` linkleri ("Hosted on Vercel") sayılmaz. JS bundle'larındaki mutlak adresler ana sayfaya ait sayılır. Kaynaklara istek atılmaz (pasif). CDN tespiti: `cf-ray`, `x-vercel-cache`, `x-served-by`, `via`, `x-amz-cf-id`, `cf-cache-status` header'ları veya `server: cloudflare/cloudfront/akamai/fastly`.
- **empty_server_html:** Ana içerik = body'den script/style/noscript/template/svg ile header/nav/footer/aside ve navigation/banner/contentinfo rolleri çıkarıldıktan sonra kalan metin. Eşik kontrolü `<h1>` dışındaki kelimelerle yapılır (`LC_WEB_MIN_MAIN_CONTENT_WORDS`, 40). Boş SPA kabuğu → high. `reference_median_words` sadece en az 3 sağlıklı sayfa varsa hesaplanır, yoksa 0. Atlanan yollar (`LC_WEB_RENDER_SKIP_PATHS`) yol parçası olarak eşleşir (`/tr/login` da atlanır).
- **Gerçek doğrulama (www.metalgymturkey.com/tr):** origin_exposed high (railway, CDN arkasında), og_missing medium "Ana sayfanda ve 6 sayfada daha og:image eksik" (kategori sayfaları listede yok), empty_server_html `/tr/shop` (1 kelime) ve `/tr/studio` (sunucu HTML'inde sadece "Ekipmanlar hazırlanıyor…"). İkincisi spec'te geçmiyordu ama gerçek bir bulgu.

## 2026-11-07 — İçerik, gizlilik ve link check'leri (6 yeni)

- **Kayıtlı alan adı:** `tldextract` paketle gelen Public Suffix List snapshot'ıyla, ağa çıkmadan (`suffix_list_urls=()`) ve diske önbellek yazmadan (`cache_dir=None`, salt okunur FS) çalışır. `.org.tr`, `.com.tr` gibi iki parçalı uzantılar doğru ayrılır.
- **Tekrar eden öğeler bir kez sayılır:** Her sayfada tekrarlanan logo (img_missing_alt) `src`'ye göre, footer bülten formu (form_without_consent) action + alan türleri + sorunlara göre tekilleştirilir; aksi hâlde 11 sayfadaki aynı öğe 11 sorun sayılırdı.
- **duplicate_title_description:** noindex sayfalar atlanır (aramada görünmezler). "Çoğunluk" mesajı için `majority_same_title` kanıta eklendi. Başlık ve metin dinamiktir (`passed_title` var).
- **form_without_consent – alan tespiti:** Alan adı, id, placeholder, aria-label, autocomplete ve label metni Türkçe karakterler sadeleştirilerek kelimelere bölünür ("Adınız" → "adiniz"). Kısa anahtarlar (ad, tc, tel) sadece tam kelime eşleşir. İç içe arama ("adsoyad", "eposta") sadece 5+ harfli anahtarlarda yapılır; böylece "username" alanı "ad soyad" sayılmaz.
- **form_without_consent – giriş formu atlanır:** Şifre alanı olup kişisel alan olarak sadece e-posta içeren formlar (login) yeni kişisel veri toplamadığı için kontrol edilmez. Arama formları spec'teki gibi atlanır.
- **form_without_consent – kanıt:** Spec'teki alanlara ek olarak `issues` listesi (`insecure_action`, `no_consent`, `no_spam_protection`) var; metinler bu listeden dinamik üretilir. Alan değerleri ve CSRF token'ları hiçbir zaman yazılmaz (testle doğrulanır). Honeypot: website/url/hp/honeypot/_gotcha adlı veya satır içi CSS ile gizlenmiş, hidden olmayan input.
- **third_party_form_embed:** Kanıtta `url_host` = host + yolun ilk parçası; ilk parça sadece harflerden oluşuyorsa ve 20 karakteri geçmiyorsa saklanır (ör. `/forms`, `/r`). Rakam veya kod içeren parçalar form kimliği olabileceği için atılır (jotform.com/2312... gibi). Sayfa bazlı onay bilgisi `page_has_consent` olarak eklenir. Sadece `<a>` linki olan bulgular `info` (puanı etkilemez).
- **insecure_http_links:** Site hiç HTTPS değilse kendi alan adına giden http linkleri raporlanmaz (web.https.missing zaten raporlar). header da nav sayılır. Örnek alan adları (example.*, .test, .local, .invalid) ve localhost hariç.
- **malformed_youtube:** Video kimliği ham (decode edilmemiş) URL'den okunur; `%3F`, `%26`, `%3D`, `?`, `&`, `=` → encoded_query_in_id. Kimlik 11 karakter değilse veya geçersiz karakter içeriyorsa → invalid_length. `/embed/videoseries` (oynatma listesi), kanal ve `@handle` linkleri video sayılmaz.
- **img_missing_alt:** `data-src`/`data-lazy-src` (lazy loading) da kaynak sayılır. width/height 0 veya 1 olan pikseller ve 1 KB'tan kısa data: URI'ler atlanır.
- **Gerçek doğrulama (ulkuocaklari.org.tr):** Altı check'in hepsi spec'teki örneklerle uyumlu bulgu üretti. `/iletisim` 10 sayfa limitine girmediği için doğrudan taranarak doğrulandı (ad/e-posta/telefon, onay yok, CAPTCHA yok → medium).
