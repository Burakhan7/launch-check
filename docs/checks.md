# Check notları: neyi yakalar, neyi kaçırabilir

Her check için iki satır: tespitin dayandığı genel sinyal ve bilinen sınırları. Anahtar kelime
listeleri `apps/api/src/launchcheck/keywords/{tr,en}.yaml` dosyalarında; hiçbiri tek başına
tespit için yeterli sayılmaz, bağlam kurallarıyla birlikte kullanılır.

## Ortak sinyaller (meta)

- **collects_special_category_data / special_categories** — Yakalar: form sorularının (label, legend, seçenek, placeholder, name/id ve sorunun hemen üstündeki kısa metin) KVKK md.6 kategorilerine ait kelimeleri; bir kategori ancak 2 farklı sinyal veya 1 sinyal + seçenek listesi (select/radio) ile sayılır.
  Input yerine düğmelerle yapılmış seçenekler (React/Vue "chip" deseni: aynı kapsayıcıda en az 2 `type=button`/`role=radio` düğmesi, menü/header/footer hariç) de soru sayılır.
  Kaçırabilir: form alanları JS ile sonradan çiziliyorsa (SSR olmayan SPA) ya da sorular görsel/ikonla soruluyorsa; tek bir serbest metin alanında geçen hassas soru da sayılmaz.
- **is_dating_or_matching** — Yakalar: metinde en az 2 farklı flört/eşleşme/evlilik sinyali (biri eşleşme mekaniği: "eşleşme", "eş adayı", "matchmaking"...) + etkileşim (form sorusunda bu ifadeler ya da giriş sistemi + başlık/düğmelerde eşleşme mekaniği).
  Kaçırabilir: kendini başka kelimelerle anlatan (ör. sadece "arkadaşlık") ve giriş sistemi olmayan siteler; evlilik konulu bloglar bilinçli olarak dışarıda bırakılır.
- **has_live_media** — Yakalar: medya token'ı (sesli, görüntülü, video, kamera, ekran paylaşımı, ses odası...) ile iletişim token'ının (sohbet, görüşme, oda, kanal, chat, call...) aynı cümlede ve en fazla 8 kelime arayla geçmesini ("sesli, görüntülü ve yazılı sohbet"), ayrıca JS'te WebRTC/LiveKit/Jitsi gibi izleri.
  Kaçırabilir: özelliği sadece görselle veya JS ile sonradan yazılan metinle anlatan siteler; "görüntülü kayıt", "sesli kitap", "sesli arama" bilinçli olarak sayılmaz.
- **has_public_community** — Yakalar: "herkese açık sunucu", "topluluk kur" gibi ifadeler; ya da en az 2 farklı dizin yolu/başlığı (keşfet, sunucular, profiller, explore, communities...) veya bir dizin sinyali + giriş sistemi.
  Kaçırabilir: dizin sayfasını farklı adlandıran topluluklar; tek bir "/members" linki olan dernek siteleri bilinçli olarak sayılmaz.
- **has_login** — Yakalar: şifre alanı, son yol parçası giriş/kayıt olan linkler (/giris, /login, /register...) veya kısa "Giriş yap / Sign in" link metinleri.
  Kaçırabilir: sadece modal/JS ile açılan ve sunucu HTML'inde iz bırakmayan giriş ekranları.
- **Önem yükseltme** — Site özel nitelikli veri topluyorsa security kategorisindeki medium bulgular high'a çıkar (`escalated_due_to`), high bulgular "hassas veri" notuyla işaretlenir. Önemi bunu zaten hesaba katan check'ler (`considers_special_data`) yükseltilmez.

## web.content.template_placeholders

Yakalar: görünür metin ve linklerde `[Adres]`, `<<firma adı>>`, `__DATE__`, işlenmemiş `{{ }}`, örnek alan adı/e-postalar (alanadiniz, example.com), Lorem ipsum, TODO/TBD/FIXME/XXX, "buraya ... yazın", kısa öğelerde ekrana düşmüş `undefined/null/NaN`; parantez içi ancak yer tutucu kelimesi (ad, adres, e-posta, company...) taşıyorsa sayılır, yasal sayfalarda iletişim bilgisi eksikse önem en yüksek.
Kaçırabilir: yer tutucu olarak gerçek görünen uydurma bilgiler (ör. "Örnek Mah. 1. Sok.") ve JS ile sonradan yazılan metinler; istemci tarafı şablon motoru (Vue/Angular/Alpine) olan sayfalarda `{{ }}` bilinçli olarak sayılmaz.

## web.privacy.special_category_without_consent

Yakalar: özel nitelikli soruların bulunduğu formda (veya 3 üst kapsayıcısında) metni açık rıza bildiren bir onay kutusu yoksa; çok adımlı akışlarda ana sayfa CTA'sının açtığı ilk adıma da bakar, bulamazsa `multi_step_unverified`; tek kutuda yurt dışı aktarım/pazarlama gibi başka amaçlar da varsa "paket rıza" (low).
Kaçırabilir: rızanın taranmayan ara bir adımda veya JS modalda alındığı akışlar (yanlış pozitif olarak çıkar, evidence'ta işaretlenir); rıza metninin hukuki yeterliliği değerlendirilmez.

## web.safety.age_gate_missing

Yakalar: yabancılarla canlı sesli/görüntülü sohbet (canlı medya + rastgele/yabancı/eşleşme bağlamı) veya flört/eşleşme platformlarında 18 yaş ifadesi ve yaş/koşul onay kutusu yoksa; doğum yılı/tarihi alanı (select seçenekleri veya date/number alanının `max` değeri; `for`'suz kardeş label da okunur) son 18 yılı seçtiriyorsa onay geçersiz sayılır ve vurgulanır, 18 yaş altını dışlıyorsa yaş doğrulaması sayılır.
Kaçırabilir: yaş doğrulaması üçüncü taraf bir servisle veya kayıttan sonra yapılıyorsa (yanlış pozitif); kendini canlı sohbet olarak tanıtmayan, sadece JS içinde WebRTC kullanan ve yabancılarla eşleşmeyi anlatmayan uygulamalar.

## web.infra.platform_default_domain

Yakalar: sitenin kendisi `*.vercel.app`, `*.onrender.com` gibi bir platform varsayılan alt alan adındaysa (origin_exposed ile aynı liste) ve giriş, kişisel veri formu, özel nitelikli veri veya ödeme sinyali (Stripe/iyzico/PayTR script'i, sepet/ödeme linkleri) varsa.
Kaçırabilir: listede olmayan platformlar ve ödeme/giriş akışı tamamen başka bir alan adında (ör. harici ödeme sayfası) olan siteler; demo/portföy siteleri bilinçli olarak raporlanmaz.

## web.privacy.kvkk_transfer_basis

Yakalar: Türkçe yasal sayfalarda (taranan + footer'dan en fazla 3 ek sayfa) yurt dışı aktarım bölümünde açık rıza geçip metnin hiçbir yerinde standart sözleşme/yeterlilik kararı/uygun güvence geçmiyorsa; "rıza" kelimesinin yakınında hizmetin sunulamayacağı söyleniyorsa; 7499 sayılı Kanun'a atıf olup olmadığı ve geçen ülkeler bilgi olarak yazılır.
Kaçırabilir: güvenceyi başka kelimelerle anlatan metinler (yanlış pozitif) ve aktarımı ayrı bir PDF'te açıklayan siteler; bu bir metin analizidir, hukuki görüş değildir.

## web.downloads.non_release_build

Yakalar: uygulama dosyalarına (.exe/.msi/.dmg/.apk/.ipa/.AppImage..., arşivler sadece link metni "indir/download" ise) giden linklerin dosya adı veya yol parçalarında tam token olarak test/dev/debug/beta/nightly/staging/rc... geçmesi; ana sayfa hero'sundaki veya ilk 2 indirme linki high, link/yakın metinde "beta/önizleme" yazıyorsa low.
Kaçırabilir: test build'ini "app-setup.exe" gibi masum bir adla dağıtanlar ve indirmeyi JS ile başlatan butonlar. Dosyalar indirilmez, HEAD isteği bile atılmaz; imza ve sürüm kontrolü Faz 2'de (Authenticode imzası / macOS notarization).

## web.render.route_serves_root_shell

Yakalar: iç sayfanın ana sayfayla karşılaştırıldığında en az 2 sinyal taşıması: canonical köke gidiyor, title+h1 birebir aynı, ana içerik (nav/header/footer hariç) kelime 3-shingle Jaccard benzerliği ≥ %90; URL veya ana sayfadaki link metni fiyat/ürün sayfası diyorsa high. Bu sayfalar empty_server_html, duplicate_title_description, og_missing ve keyword_stuffing'den çıkarılır (`covered_by`).
Kaçırabilir: kabuk HTML'i ana sayfadan farklı olan (ör. ayrı bir boş SPA kabuğu) siteler; bunları empty_server_html yakalar. 20 kelimeden kısa içeriklerde benzerlik ölçülmez.

## web.privacy.internal_role_exposed

Yakalar: kod blokları ve menü dışındaki öğelerde metni tek başına bir büyük harf rol enum'u olan rozetler (SUPER_ADMIN, ADMIN...) ve sayfaya gömülü JSON'da (__NEXT_DATA__, application/json, window.__*_STATE__) rol alanlı kullanıcı nesneleri; rol alanıyla birlikte e-posta/telefon/hash/token alanı varsa high (sadece alan ADLARI kaydedilir).
Kaçırabilir: rolleri sadece sayfa açıldıktan sonraki API yanıtında gönderen uygulamalar; "Yönetici" gibi kullanıcıya yönelik etiketler bilinçli olarak sayılmaz.

## web.links.dead_or_inconsistent_nav

Yakalar: nav/header/footer'da href'siz, "#" veya javascript: linkler (açılır menü/modal tetikleyicileri hariç) ve link listesinde kardeşleri link olan ama kendisi link olmayan 1-3 kelimelik öğeler (sütun başlıkları ve telif satırları hariç); diğer sayfaların footer'ında olup ana sayfada olmayan yasal/destek linkleri.
Kaçırabilir: JS ile tıklama dinleyicisi eklenmiş (HTML'de iz bırakmayan) öğeler yanlış pozitif verebilir; link hedefinin çalışıp çalışmadığı (404) Faz 1'de kontrol edilmez.

## web.conversion.cta_targets_login

Yakalar: ana sayfada link hedeflerini son yol parçası veya ?mode= değerine göre giriş/kayıt olarak sınıflandırıp menüde sadece giriş olmasını, son section'daki ilk CTA'nın girişe gitmesini veya belirgin CTA'ların (btn/cta/primary class'lı, hero ve son section linkleri) çoğunun girişe gitmesini; hiç kayıt yolu ve kayıt formu yoksa info.
Kaçırabilir: giriş ve kaydı aynı sayfada sekme olarak sunan siteler (/auth gibi nötr yollar sınıflandırılmaz) ve CTA'ları JS ile yönlendiren butonlar.

## web.conversion.empty_public_listing

Yakalar: URL'si veya h1'i dizin/liste bildiren (keşfet, üyeler, profiller, sunucular, ürünler, yorumlar...) indekslenebilir sayfalarda en büyük tekrarlanan kart grubunun 6'dan az olması, 20'nin altındaki "12 üye" gibi görünür sayaçlar ve "henüz hiç ... yok" boş durum metinleri.
Kaçırabilir: listeyi tamamen JS ile sonradan yükleyen sayfalar (0 öğe ve boş durum metni yoksa bulgu üretilmez) ve kart yapısı tekrarlanmayan özel tasarımlar; noindex sayfalar vitrin sayılmaz.

## web.conversion.keyword_stuffing

Yakalar: nav/header/footer hariç en az 100 kelimelik sayfalarda 3-6 kelimelik bir öbeğin en az 4 kez ve metnin %3'ünden fazlasını oluşturacak sıklıkta geçmesi (sadece marka adından oluşan öbekler hariç); meta keywords'te 10'dan fazla giriş veya aynı kelimeyi paylaşan 4+ varyant ek bilgi olarak yazılır.
Kaçırabilir: aynı fikri farklı kelimelerle tekrarlayan doldurma ve 100 kelimeden kısa sayfalar (fiyat kartları gibi doğal tekrarlar için bilinçli sınır).

## web.conversion.value_proposition (Faz 2, LLM)

Faz 1'de kayıtlıdır ama çalışmaz (`phase = 2`, LLM istemcisi bağlı değil). Kod: `apps/api/src/launchcheck/checks/conversion/llm/`.

**Girdi** (`ValuePropositionInput`, sadece ana sayfadan): url, title, meta description, meta keywords, h1-h3 başlıkları, ilk ekran (hero) metni, CTA metinleri ve hedefleri, nav ve footer link metinleri, meta bayrakları (is_ugc, has_login, is_dating_or_matching, collects_special_category_data, has_public_community).

**Rubrik** (her madde 0-2 puan + kısa gerekçe + somut öneri):

1. **what** — Ürünün ne yaptığı ilk ekranda 1 cümlede anlaşılıyor mu?
2. **who** — Hedef kitle belli mi?
3. **why** — Rakiplere veya mevcut alternatife göre farkı söyleniyor mu? Meta keywords/title'da rakip/alternatif sinyali ("alternatif", "alternative", "vs", rakip marka) var ama görünür metinde fark anlatılmıyorsa `positioning_hidden_in_meta`.
4. **trust** — Arkasında kim olduğu (şirket/kişi), verinin nerede tutulduğu, sosyal kanıt.
5. **single_action** — İlk ekranda tek ve net bir birincil CTA var mı?
6. **pricing** — Ücretli plan varsa fiyat ana sayfadan 1 tıkla ve sunucu HTML'inde görülebiliyor mu?
7. **consistency** — Marka/şirket bilgisi, iddialar ve sayılar sayfalar arasında çelişiyor mu?

**Çıktı** (`ValuePropositionOutput`, JSON):

```json
{
  "scores": { "what": { "score": 0, "reason": "...", "suggestion": "..." } },
  "findings": [
    {
      "rubric_item": "why",
      "severity": "high | medium | low | info",
      "evidence_text": "girdide AYNEN geçen metin",
      "suggestion": "...",
      "rewrite_example": "önerilen başlık/alt başlık",
      "needs_verification": false,
      "positioning_hidden_in_meta": false
    }
  ]
}
```

**Uydurmaya karşı kurallar:** LLM sadece verilen metne dayanır, dış bilgi kullanmaz; piyasa/rakip bağlamı gerektiren yorumlar `needs_verification: true` ("doğrulanmalı"); öneri metninde rakip marka adı varsa "(marka hukuku açısından kontrol et)" notu. Şemaya uymayan çıktı reddedilir; `evidence_text`'i girdide geçmeyen bulgular atılır.

## web.content.framework_leftovers

Yakalar: ana sayfa ana içeriğinde (nav/header/footer hariç) Vite/CRA/Next.js/Lovable/Bolt şablon cümleleri (sunucu HTML'i boş SPA'larda aynı cümleler JS bundle'ında), varsayılan `<title>` ("Vite + React + TS", "Create Next App", "vite_react_shadcn_ts"...) ve açıklamalar, Lovable `author`/`twitter:site` etiketleri, aracın alan adındaki og:image, `/vite.svg`·`/next.svg`·`/vercel.svg` ikonları ve hash'i bilinen varsayılan favicon'lar.
Kaçırabilir: hash'i listede olmayan eski şablon sürümlerinin favicon'ları, metni biraz değiştirilmiş şablon sayfaları ve listede olmayan araçlar; kod bloklarında (`pre`, cümleyi tek başına taşıyan `code`) geçen şablon metni ve aracın kendi sitesi bilinçli olarak sayılmaz.

## web.meta.viewport_missing

Yakalar: ana sayfa ve taranan iç sayfalarda `meta[name=viewport]` yokluğu, sayısal sabit `width`, `device-width` olmayan genişlik ve yakınlaştırma engeli (`user-scalable=no/0` veya `maximum-scale` < 2); anahtar/değerler büyük-küçük harf ve boşluk duyarsız okunur.
Kaçırabilir: viewport'u JS ile sonradan ekleyen sayfalar (yanlış pozitif) ve etiketi doğru olup CSS'i sabit genişlikli olan sayfalar (Faz 2'de tarayıcıyla ölçülür).

## web.secrets.db_connection_string / apk.secrets.db_connection_string

Yakalar: JS bundle/inline script (web) veya APK string kaynaklarında kullanıcı adı + şifre içeren `postgres://`, `mysql://`, `mariadb://`, `mongodb(+srv)://`, `redis(s)://`, `amqp(s)://` adresleri; yönetilen DB servisleri (Supabase, Neon, PlanetScale, MongoDB Atlas, Upstash, Railway, Render, Aiven, RDS, CockroachDB) critical, diğer herkese açık host'lar high. Kanıtta şifre, kullanıcı adı ve tam host yok; sadece tür, kayıtlı alan adı ve şifre uzunluğu.
Kaçırabilir: parçalara bölünerek birleştirilen adresler ve şifresi ayrı değişkende duran bağlantılar (o durumda şifre `leaked_key` desenlerine de uymayabilir); yer tutucu şifreler, 6 karakterden kısa veya kullanıcı adıyla aynı şifreler ve yerel host'lar (localhost, docker servis adları, .local/.internal/.test) bilinçli olarak sayılmaz (yereller `meta.local_db_strings`).

## web.secrets.leaked_key / apk.secrets.leaked_key — Ek Paket 1 desenleri

Yakalar (ek): Hugging Face, Mapbox secret (`sk.`), Slack token ve webhook, Discord webhook, Telegram bot token, Mailgun, Cloudinary URL (kanıtta API secret yok), npm token; bağlamlı olarak iyzico (aynı dosyada 2000 karakter yakınında "iyzico/iyzipay" varsa; `sandbox-` → medium) ve PayTR merchant key/salt. kind=stripe_* bir Clerk anahtarı da olabilir (Clerk aynı `sk_live_`/`sk_test_` önekini kullanır).
Kaçırabilir: bağlam kelimesi farklı dosyada olan iyzico/PayTR anahtarları ve değişken adı farklı yazılmış (`apiSecret` gibi) anahtarlar; Mapbox public token (`pk.`) bilinçli olarak sayılmaz.

## web.files.api_docs_exposed

Yakalar: `LC_WEB_API_DOC_PATHS` yollarında kökünde `openapi`/`swagger` + `paths` olan JSON (kesilmişse ilk 4 KB'ta imza) veya `swagger-ui`/`SwaggerUIBundle`/`redoc` içeren HTML; HTML sayfadaki aynı origin spec adresi 1 ek istekle okunur. Güvenlik şeması yok + admin/internal/debug/users/delete/export/upload yolları → high.
Kaçırabilir: listede olmayan yollarda (ör. `/api/v2/docs`) veya giriş arkasında duran dokümanlar ve GraphQL introspection (Faz 3); pazarlama `/docs` sayfaları bilinçli olarak sayılmaz.

## web.files.dev_files_exposed

Yakalar: içeriği doğrulanan `.npmrc` (token varsa, critical), `phpinfo.php`/`info.php` (high), `docker-compose.yml/.yaml` (`services:` satırı, medium; sadece servis adları kaydedilir), `.DS_Store`, `.vscode/settings.json`, `.idea/workspace.xml`, `.firebaserc` (low). SPA catch-all ve rastgele yol (baseline) yanıtıyla aynı içerik elenir.
Kaçırabilir: listede olmayan geliştirme dosyaları ve sunucunun 403 ile gizleyip başka yoldan sunduğu dosyalar; token içermeyen `.npmrc` bilinçli olarak raporlanmaz.

## apk.manifest.example_package

Yakalar: `com.example` ad alanındaki paketler (Play reddeder, high) ve `com.mycompany`, `com.yourcompany`, `com.company`, `com.test`, `com.demo`, `io.flutter` şablon ad alanları (low); önek tam segment olarak eşleşir (`com.examplecorp` sayılmaz).
Kaçırabilir: listede olmayan şablon adları (ör. `com.myapp.app`); manifest okunamadıysa bulgu üretilmez.

## Faz 2 — render modülü (gerçek tarayıcı)

Ana sayfa + Faz 1 sayfaları + render sonrası menü/iç linkler (en fazla 25), her biri mobil (375×812) ve masaüstünde (1440×900); ana sayfa ve en fazla 2 kilit sayfa (fiyat > kayıt > ürün > ilk menü linki) ayrıca 320/768/1024 genişliklerinde ölçülür. Ölçümler sayfa içinde yapılır; storage ve çerez değerleri sayfadan hiç çıkmaz. Bot doğrulama sayfası gelirse modül çalışmaz (`meta.deep_blocked`).

### web.render.js_runtime_errors
Yakalar: yakalanmamış istisnalar ve `console.error` mesajları (kaynak yükleme ve CORS mesajları hariç; onlar failed_requests'te), sayılar/URL'ler normalize edilerek tekilleştirilir; ana sayfa veya fiyat/kayıt sayfasındaysa high.
Kaçırabilir: sadece kullanıcı etkileşiminden (tıklama, form) sonra oluşan hatalar; sadece belirli tarayıcı/cihazlarda oluşan hatalar.

### web.render.failed_requests
Yakalar: sitenin kendi alan adına giden 4xx/5xx ve ağ hataları, herhangi bir host'a giden fetch/xhr (API) hataları, CORS hataları (console mesajıyla eşleştirilir); API isteği başarısızsa high.
Kaçırabilir: oturum gerektiren API'ler (giriş yapılmadığı için 401 normaldir ve 4xx olarak görünür; yanlış pozitif olabilir), ölçüm/reklam servisleri (bilinçli olarak hariç), görseller (broken_images'ın işi), bizim proxy'mizin engellediği iç adresler.

### web.render.spa_deep_link_404
Yakalar: ana sayfada linki olan bir iç sayfa doğrudan açılınca 404 dönüyor ama ana sayfadan tıklanınca adres değişip içerik (≥10 kelime) çiziliyorsa.
Kaçırabilir: ana sayfada linki olmayan sayfalar; tıklamayla açılması bir kullanıcı etkileşimi (menü açma vb.) gerektirenler.

### web.render.visible_junk_text
Yakalar: görünür, 60 karakterden kısa metin düğümlerinde `undefined`, `NaN`, `null`, `[object Object]`, `{{ }}`, `Invalid Date` (kod/pre/textarea hariç; büyük-küçük harf duyarlı, kelime sınırlı).
Kaçırabilir: uzun cümlelerin içine düşmüş değerler (yanlış pozitifi önlemek için bilinçli) ve sadece etkileşimden sonra görünen alanlar.

### web.render.broken_images
Yakalar: yüklenmesi biten ama `naturalWidth = 0` olan görseller (data: URI ve gizli görseller hariç); ilk ekranda ve geniş ise (hero) medium.
Kaçırabilir: lazy-load ile hiç yüklenmemiş görseller (henüz tamamlanmadığı için) ve CSS arka plan görselleri.

### web.mobile.horizontal_overflow
Yakalar: 320/375 px'te layout viewport genişliği (`clientWidth`) 4 px'ten fazla aşılıyorsa; sorumlu en dış öğeler (overflow-x hidden/auto kapsayıcı içindekiler hariç).
Kaçırabilir: sadece etkileşimle açılan öğelerin taşması; viewport etiketi olmayan sayfalar (sayfa 980 px'te çizilir, viewport_missing'in konusu).

### web.mobile.overlapping_elements
Yakalar: kendi metni olan veya etkileşimli görünür öğelerden ikisinin kesişimi küçük olanın alanının yarısından fazlaysa (iç içe öğeler, fixed/sticky katmanlar ve aria-hidden hariç); aynı öğe çifti tüm sayfalarda bir kez sayılır.
Kaçırabilir: metni görsel olarak üst üste binen ama kutuları kesişmeyen durumlar (ör. negatif harf aralığı); bilinçli tasarlanmış üst üste rozetler yanlış pozitif olabilir.

### web.mobile.cta_below_fold
Yakalar: mobil ana sayfada menü dışındaki ilk "btn/cta/primary" sınıflı veya hero içindeki eylem öğesinin üst kenarı ilk ekranın altındaysa.
Kaçırabilir: CTA'sı bu sınıf adlarını kullanmayan ve hero dışında duran sayfalar (CTA bulunamazsa bulgu üretilmez).

### web.mobile.tap_targets_small
Yakalar: mobilde 24×24 px'ten küçük en az 3 tıklanabilir öğe olan sayfalar (cümle içindeki satır içi linkler WCAG istisnası gereği hariç).
Kaçırabilir: yeterince büyük ama birbirine çok yakın hedefler (aralık ölçülmez).

### web.ux.custom_404_missing
Yakalar: Faz 1'in rastgele yolu render edildiğinde nginx/Apache/Express/Vercel/Netlify/IIS varsayılan sayfası veya 15 kelimeden kısa ve ana sayfa linki olmayan sayfa.
Kaçırabilir: 200 ile ana sayfayı gösteren SPA'lar (soft 404; bu check'in konusu değil) ve varsayılan sayfası listede olmayan sunucular.

### web.ux.offline_breaks_page
Yakalar: ana sayfa yüklendikten sonra bağlantı kesilip en fazla 2 menü linkine tıklanınca boş ekran veya sonsuz yükleme göstergesi kalıyorsa (hata/çevrimdışı mesajı varsa sorun yok).
Kaçırabilir: verisini önbellekten gösteren sayfalar; klasik (her tıklamada sayfa yüklenen) sitelerde tarayıcının kendi çevrimdışı sayfası çıktığı için bilinçli olarak raporlanmaz.

### web.security.token_in_web_storage
Yakalar: localStorage/sessionStorage'da JWT içeren (JSON içinde gömülü olanlar dahil) veya adı token/auth/session/jwt/access/refresh geçen, 16 karakterden uzun değerler; JWT 30 günden uzun geçerliyse high. Sadece anahtar adı, uzunluk ve kalan gün kaydedilir.
Kaçırabilir: giriş yapılmadan oluşmayan oturumlar (Faz 1-2 giriş yapmaz; Faz 4'te test hesabıyla) ve IndexedDB'de tutulan token'lar.

### web.security.insecure_cookie_flags
Yakalar: sitenin kendi alan adındaki, adı oturum benzeri (sess/sid/auth/token/jwt/login/remember/_session) çerezlerde HttpOnly eksikliği, HTTPS sitede Secure eksikliği, Secure'suz SameSite=None (ölçüm çerezleri hariç).
Kaçırabilir: giriş yapınca oluşan çerezler ve adı oturum belli etmeyen çerezler.

### web.security.client_only_admin_route
Yakalar: Faz 1 bundle'larında ve tarayıcının yüklediği aynı origin JS'lerde geçen `/admin`, `/yonetim`, `/panel`, `/backoffice`, `/dashboard/admin`, `/cms` rotaları açılınca (tıklama yok) giriş formu ve login yönlendirmesi olmadan "admin/yönetim/dashboard" başlığı + tablo veya sil/düzenle düğmeleri görünüyorsa high; sadece başlık varsa info ("Faz 3'te doğrulanacak").
Kaçırabilir: farklı adlandırılmış yönetim rotaları; veriyi sadece giriş sonrası API'den yükleyen paneller (boş iskelet görünür, info'ya düşer).

### web.security.sourcemap_tree_exposed
Yakalar: Faz 1'in açık bulduğu map dosyalarının `sources` dizisinden kaynak dosya yolları (node_modules hariç, en fazla 50 örnek + toplam); Faz 1 bulgusunu `escalated` yapar.
Kaçırabilir: map'te `sources` alanı 1 MB'tan sonra başlıyorsa (alan genelde başta olduğu için nadir).

### Faz 1 doğrulama kuralları (render)

- `web.render.empty_server_html` → render sonrası ana içerik kelime sayısı sunucu HTML'inin 3 katından fazlaysa `confirmed`.
- `web.render.route_serves_root_shell` → render sonrası title, h1 veya canonical sayfaya özgü hâle geliyorsa `confirmed`.
- `web.meta.og_missing` → ana sayfa render edildiyse `confirmed` (paylaşım botları JS çalıştırmaz; JS ile eklenen etiket sorunu çözmez).
- `web.files.sourcemap_exposed` → map'lerden kaynak ağacı okunabildiyse `escalated`.
- `web.meta.viewport_missing` (eksik / sabit / cihaz genişliği yok) → mobilde layout genişliği cihaz genişliğine eşitse (etiket JS ile ekleniyor) `refuted`, değilse `confirmed`; sadece zoom engeli varsa karar verilmez.
- `web.bundle.localhost_urls` → sayfa render sırasında localhost/LAN adresine gerçekten istek attıysa (proxy engelledi) `escalated`, atmadıysa `confirmed`.

## Faz 2 — hız modülü (Lighthouse + CrUX)

Lighthouse 13 (mobil: simüle yavaş 4G + 4× CPU) ana sayfada 3 kez mobil + 1 kez masaüstü, render modülünün bulduğu kilit sayfada (fiyat > kayıt > ürün > ilk menü linki) 3 kez mobil çalışır; ortanca alınır. Chromium'u biz başlatırız (render ile aynı güvenlik bayrakları, tüm trafik egress proxy'den); Lighthouse `--port` ile bağlanır. CrUX (Google'ın gerçek kullanıcı verisi, p75) sırasıyla sayfa/telefon → origin/telefon → origin/tüm cihazlar sorgulanır. Kanıttaki "tahmini kazanç" değerleri Lighthouse'undur. Bütçe yetmeyecekse yeni ölçüm başlatılmaz; aynı adres 2 kez başarısız olursa (zaman aşımı, NO_FCP, 4xx/5xx ana belge — bot doğrulaması dahil) tekrar denenmez.

### web.perf.lcp_slow
Yakalar: saha verisi varsa CrUX p75 LCP'si, yoksa ölçülen sayfaların en kötü laboratuvar ortancası Google sınırlarına göre (> 2,5 sn medium, > 4 sn high); kanıta LCP'ye en çok kazanç sağlayacak Lighthouse önerileri (önbellek hariç, ilk ziyarette etkisiz).
Kaçırabilir: sadece başka sayfalarda (ölçülmeyen) yavaş olan siteler; laboratuvar ölçümü tek konumdan ve sabit ağ benzetimiyle yapılır, gerçek kullanıcı dağılımını yansıtmaz (saha verisi bunun için önceliklidir).

### web.perf.cls_high
Yakalar: saha CrUX p75 CLS'si veya laboratuvar ortancası (> 0,1 medium, > 0,25 high).
Kaçırabilir: kullanıcı kaydırınca/etkileşince oluşan kaymalar laboratuvarda görülmez (saha verisi görür).

### web.perf.inp_slow
Yakalar: sadece CrUX p75 INP (> 200 ms medium, > 500 ms high).
Kaçırabilir: CrUX'ta yer almayan (az trafikli) siteler — bu durumda check "geçti" sayılmaz, `checks_skipped` olur.

### web.perf.blank_screen_long
Yakalar: max(simüle FCP, filmstrip'te ilk anlamlı kareye kadar gözlenen süre) > 3 sn; kare "boş" sayılırsa baskın renkten belirgin farklı pikseller %1,5'in altındadır (beyaz ekran, ortada küçük yükleniyor işareti). Ana sayfaysa kanıtta filmstrip asset'i.
Kaçırabilir: tek renk büyük bir arka plan + küçük logo gibi "yarı boş" ilk ekranlar (içerik sayılabilir); iki alt sınırın büyüğü kullanıldığı için gerçek mobil süre daha uzun olabilir (bilinçli: yanlış pozitif üretmemek için).

### web.perf.unused_js
Yakalar: Lighthouse coverage'ına göre açılışta çalışmayan JS > %50 ve > 200 KB (sayfaların en kötüsü); dosyalar üçüncü taraf işaretiyle.
Kaçırabilir: etkileşimde kullanılacak kod da "kullanılmayan" sayılır (Lighthouse'un sınırı); bu yüzden eşik yüksek tutuldu.

### web.perf.oversized_images
Yakalar: Lighthouse `image-delivery-insight` toplam tasarrufu ≥ 100 KB (gösterilenden büyük, eski format, sıkıştırma) veya ≥ 3 boyutsuz (width/height'sız) görsel; kanıtta gerçek ve gösterilen boyut.
Kaçırabilir: lazy-load ile ilk açılışta yüklenmeyen görseller ve CSS arka plan görsellerinin bir kısmı.

### web.perf.no_compression_or_cache
Yakalar: aktarılan boyutu gerçek boyutunun ≥ %90'ı olan ≥ 10 KB'lık metin dosyaları (JS/CSS/HTML/JSON/SVG; toplam ≥ 30 KB) ve sitenin kendi alan adındaki, önbellek süresi 1 günden kısa statik dosyalar (toplam ≥ 100 KB; üçüncü taraflar hariç).
Kaçırabilir: zaten küçültülmüş ve sıkıştırması az kazandıran dosyalar (sıkıştırılmamış olsalar da oran eşiği yakalar); CDN'in sadece bazı bölgelerde önbelleklediği dosyalar.

### web.perf.heavy_third_party
Yakalar: ana sayfanın ortanca mobil ölçümünde başka alan adlarından (Lighthouse varlık gruplaması) gelen kodun toplamı > 500 KB veya ana thread süresi > 1 sn; en ağır 5 servis.
Kaçırabilir: aynı şirketin farklı alan adındaki kendi CDN'i üçüncü taraf sayılabilir (yanlış pozitif); sadece etkileşimle yüklenen widget'lar.

## Faz 2 — dış görünürlük modülü (crt.sh, DNS, Wayback, RDAP, sertifika)

Beş parça paralel çalışır: alt alan adları (crt.sh sertifika kayıtları → DNS), e-posta DNS'i, Wayback arşivi, RDAP, TLS sertifikası. Bir parçanın servisi hata verirse, zaman aşımına uğrarsa veya global hız sınırına takılırsa sadece o parçanın check'leri atlanır (`checks_skipped`, raporda "geçti" sayılmaz). Hedef ortak barındırmadaysa (Public Suffix List'in private bölümü veya `LC_DEEP_SHARED_HOST_SUFFIXES`: x.vercel.app, x.lovable.app...) alan adı platformundur. Bu durumda sadece Wayback çalışır; başka kullanıcıların uygulamaları asla taranmaz. Hedefe ve alt alan adlarına sadece normal ziyaretçi GET'i gider.

### web.external.forgotten_subdomain
Yakalar: crt.sh'taki adlardan DNS'i herkese açık IP'ye çözülen en fazla 8 alt alan adında (staging/dev/admin/eski/yedek... önce) Faz 1'in dosya, anahtar, localhost ve HTTPS check'lerinin bulduğu sorunlar; ana sayfası 4xx/5xx dönen API alt alan adlarında da dosya/HTTPS check'leri çalışır. Önem: alt bulguların en yükseği; sadece `noindex` (staging için doğru davranış) bulgu üretmez.
Kaçırabilir: sertifikası hiç alınmamış (sadece HTTP) veya wildcard sertifikanın arkasındaki adlar (crt.sh'ta görünmez); 8'den fazla canlı alt alan adı olan sitelerde öncelik dışı kalanlar.

### web.external.subdomain_takeover_risk
Yakalar: CNAME zinciri parmak izi listesindeki bir servise (can-i-take-over-xyz, CC BY 4.0) yönelen ve servisin "proje/uygulama yok" sayfasını döndüren veya CNAME hedefi NXDOMAIN olan (Azure, Elastic Beanstalk gibi) adlar. Kaynakta "Edge case" olan servisler (GitHub Pages, Heroku, Netlify, Vercel...) medium, "Vulnerable" olanlar high.
Kaçırabilir: listede olmayan servisler (Cloudflare Pages, Render, Fly.io: doğrulanmış parmak izi yok), A kaydıyla bırakılmış eski bulut IP'leri, crt.sh'ta görünmeyen adlar.

### web.external.email_spoofing
Yakalar: MX var ve SPF yok / `+all` / DMARC yok / `p=none` → high; birden fazla SPF kaydı veya 10 DNS sorgusu sınırını aşan SPF (include zinciri sayılır) → medium; MX yoksa (null MX dahil) aynı eksikler → low. `-all`/`~all` + DMARC quarantine/reject yanlış alarm vermez.
Kaçırabilir: DKIM (seçici adı bilinmeden sorgulanamaz) ve DMARC'ın `sp=`/`pct=` ayrıntıları; e-postayı alt alan adından gönderen siteler (sadece kayıtlı alan adına bakılır).

### web.external.archived_sensitive_paths
Yakalar: Wayback CDX'te (statü 200, en fazla 5000 satır) `.env`, `.git/HEAD|config`, `.sql`, yedek arşivleri (backup/yedek/dump/db + zip/tar/gz/bak), hassas yapılandırma dosyaları, phpinfo ve API dokümanı yolları. En fazla 15 tanesi şimdi sorgu dizesi olmadan tekrar istenir ve Faz 1'in içerik doğrulamasından geçer (SPA catch-all elenir): hâlâ açık → high, artık yok → info ("o dönemki anahtarları değiştirdin mi?").
Kaçırabilir: arşivlenmemiş veya robots.txt ile arşivden çıkarılmış yollar; denenen 15 yolun dışında kalanlar (raporda gösterilmez çünkü şu anki durumları bilinmiyor). `/admin` ve `/api/` yolları bilinçli olarak alınmadı (aşağıda decisions).

### web.external.domain_expiry_soon
Yakalar: RDAP `expiration` tarihi < 30 gün → high, < 60 gün → medium; süresi dolmuşsa high.
Kaçırabilir: RDAP sunmayan uzantılar (ör. .tr: check atlanır) ve otomatik yenilemesi açık alan adları (bitişe yakın görünür ama yenilenecektir: metin bunu kontrol ettirir).

### web.external.cert_expiry_soon
Yakalar: sitenin 443 portundan alınan sertifikanın kalan süresi; eşikler min(14 gün, ömrün %20'si) → high ve min(30 gün, ömrün %30'u) → medium. Bu sayede 90/45/6 günlük otomatik sertifikalar normal yenileme penceresinde alarm vermez.
Kaçırabilir: alt alan adlarının ve CDN'in farklı bölgelerdeki sertifikaları (sadece hedef host, tek IP); geçersiz sertifika burada değil Faz 1 HTTPS check'lerinde.
