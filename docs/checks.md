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
