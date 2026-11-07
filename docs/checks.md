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
