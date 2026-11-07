"""Canlı medya/topluluk sinyalleri ve 7 yeni check + Faz 2 value_proposition iskeleti:
non_release_build, route_serves_root_shell (+ tekilleştirme), internal_role_exposed,
dead_or_inconsistent_nav, cta_targets_login, empty_public_listing, keyword_stuffing."""

import json

import pytest

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.checks.conversion.llm.schema import RUBRIC, RubricItem, ValuePropositionOutput
from launchcheck.checks.conversion.llm.value_proposition import (
    GUARDRAILS,
    ValueProposition,
    build_input,
    build_prompt,
    parse_output,
)
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.checks.shared.text import fold
from launchcheck.checks.web.conversion import (
    CtaTargetsLogin,
    EmptyPublicListing,
    KeywordStuffing,
    auth_target,
    visible_counters,
)
from launchcheck.checks.web.downloads import NonReleaseBuild, nonrelease_tokens
from launchcheck.checks.web.exposure import InternalRoleExposed
from launchcheck.checks.web.meta import OgMissing
from launchcheck.checks.web.navigation import DeadOrInconsistentNav
from launchcheck.checks.web.render import EmptyServerHtml, RouteServesRootShell
from launchcheck.checks.web.safety import AgeGateMissing
from launchcheck.checks.web.seo_content import DuplicateTitleDescription
from launchcheck.collectors.web_signals import analyze_content, live_media_mentions
from launchcheck.config import get_settings
from launchcheck.i18n import finding_text, fragment_fn
from tests.checks.helpers import FIXTURES_WEB, make_ctx, run

FIX = FIXTURES_WEB / "conversion"
S = "https://sohbetli.com.tr"
NEW_IDS = (
    "web.downloads.non_release_build",
    "web.render.route_serves_root_shell",
    "web.privacy.internal_role_exposed",
    "web.links.dead_or_inconsistent_nav",
    "web.conversion.cta_targets_login",
    "web.conversion.empty_public_listing",
    "web.conversion.keyword_stuffing",
    "web.conversion.value_proposition",
)


def html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def sohbetli_ctx(**kwargs: object) -> object:
    """Örnek siteye benzeyen Next.js topluluk uygulaması: /turbo ana sayfanın kopyası."""
    home = html("next_community_home.html")
    return make_ctx(
        html=home,
        final_url=f"{S}/",
        pages=[
            (f"{S}/turbo", home),
            (f"{S}/directory/profiles", html("next_community_profiles.html")),
        ],
        **kwargs,  # type: ignore[arg-type]
    )


def single(url: str, name: str, **kwargs: object) -> object:
    return make_ctx(html=html(name), final_url=url, **kwargs)  # type: ignore[arg-type]


def rendered(check_id: str, evidence: dict[str, object], lang: str = "tr") -> str:
    check = load_builtin_checks().get(check_id)
    assert check is not None
    text = finding_text(check_id, lang, check.text_params(evidence, fragment_fn(check_id, lang)))
    joined = " ".join([text.title, text.description, *text.fix_steps, text.fix_prompt])
    assert "{" not in joined, joined
    return text.title


# --- A. canlı medya ve topluluk


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("Arkadaşlarınla sesli, görüntülü ve yazılı sohbet et.", True),
        ("Ses odaları kur, ekran paylaşımı yap ve görüntülü görüşme başlat.", True),
        ("Hop on a video call or join a voice channel.", True),
        ("Düğününüz için profesyonel görüntülü kayıt hizmeti.", False),
        ("Binlerce sesli kitap dinle.", False),
        ("Sesli arama ile ürün bul.", False),  # sesli arama (voice search) iletişim değil
    ],
)
def test_live_media_proximity(text: str, hit: bool) -> None:
    assert bool(live_media_mentions(fold(text))) is hit


def test_community_and_live_flags_on_sohbetli() -> None:
    s = analyze_content(
        [
            (f"{S}/", html("next_community_home.html")),
            (f"{S}/directory/profiles", html("next_community_profiles.html")),
        ],
        [],
    )
    assert s.has_live_media and s.has_public_community and s.has_login


def test_community_needs_more_than_one_path_signal() -> None:
    page = '<html><body><a href="/members">Üyelerimiz</a><p>Dernek hakkında bilgiler.</p></body></html>'
    assert analyze_content([("https://dernek.org/", page)], []).has_public_community is False


async def test_age_gate_public_community_with_live_rooms_is_high() -> None:
    (finding,) = await run(AgeGateMissing, sohbetli_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH
    assert "public_community_live" in finding.evidence["platform_types"]
    rendered(finding.check_id, finding.evidence)


async def test_age_gate_not_for_video_recording_service() -> None:
    page = (
        "<html><body><h1>Görüntülü kayıt hizmeti</h1><p>Düğün ve nişanlarınız için görüntülü "
        "kayıt. Topluluk kur değil, anı biriktir.</p><a href='/login'>Giriş</a></body></html>"
    )
    assert await run(AgeGateMissing, make_ctx(html=page, final_url="https://kayit.com/")) == []


# --- B. web.downloads.non_release_build


async def test_downloads_test_build_in_hero_is_high() -> None:
    (finding,) = await run(NonReleaseBuild, sohbetli_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH
    (link,) = finding.evidence["links"]
    assert link["url"].endswith("sohbetli-test-setup.exe")
    assert link["tokens"] == ["test"] and link["is_primary_cta"] is True
    assert finding.category == Category.PROD_READINESS
    rendered(finding.check_id, finding.evidence)


async def test_downloads_wordpress_beta_low_nightly_medium() -> None:
    (finding,) = await run(
        NonReleaseBuild, single("https://notdefteri.com/indir/", "wp_downloads.html")
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    by_url = {link["url"].rsplit("/", 1)[1]: link for link in finding.evidence["links"]}
    assert set(by_url) == {"notdefteri-beta.msi", "notdefteri-nightly.AppImage"}
    assert by_url["notdefteri-beta.msi"]["labeled_as_beta"] is True
    # setup-latest.exe, 1.2.0.dmg ve "test sonuçları" PDF'i sayılmaz


async def test_downloads_plain_html_dev_zip_with_download_text() -> None:
    (finding,) = await run(
        NonReleaseBuild, single("https://datatool.io/", "plain_downloads_home.html")
    )  # type: ignore[arg-type]
    (link,) = finding.evidence["links"]  # source.zip: indirme metni yok → uygulama dosyası değil
    assert link["tokens"] == ["dev"]


async def test_downloads_never_fetched() -> None:
    ctx = sohbetli_ctx()
    await run(NonReleaseBuild, ctx)  # type: ignore[arg-type]
    assert ctx.fetcher.calls == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("url", "tokens"),
    [
        ("https://x.com/app-setup-latest.exe", []),
        ("https://x.com/App-1.2.0.dmg", []),
        ("https://x.com/v2/app-1.2.0-rc.1.msi", ["rc"]),
        ("https://x.com/builds/staging/app.apk", ["staging"]),
        ("https://x.com/contest/app.exe", []),  # "contest" ≠ "test"
    ],
)
def test_nonrelease_tokens(url: str, tokens: list[str]) -> None:
    assert nonrelease_tokens(url) == tokens


# --- C. web.render.route_serves_root_shell + tekilleştirme


async def test_root_shell_pricing_page_high() -> None:
    (finding,) = await run(RouteServesRootShell, sohbetli_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH and finding.category == Category.CONVERSION
    (page,) = finding.evidence["pages"]
    assert page["url"] == f"{S}/turbo"
    assert page["is_pricing_or_product"] is True  # ana sayfadaki "Turbo'ya yükselt" linki
    assert {"canonical_to_root", "same_title_h1"} <= set(page["signals"])
    rendered(finding.check_id, finding.evidence, "en")


async def test_root_shell_spa_pricing_and_landing_variant() -> None:
    home = html("spa_prerendered_home.html")
    ctx = make_ctx(
        html=home,
        final_url="https://taskly.app/",
        pages=[
            ("https://taskly.app/pricing", home),
            ("https://taskly.app/agencies", html("spa_landing_variant.html")),
        ],
    )
    (finding,) = await run(RouteServesRootShell, ctx)
    assert [p["url"] for p in finding.evidence["pages"]] == ["https://taskly.app/pricing"]
    # /agencies: benzer içerik ama kendi canonical'ı ve başlığı var → tek sinyal, bulgu yok


async def test_root_shell_plain_static_host_medium() -> None:
    home = (
        '<html><head><title>Ahşap Atölye</title><link rel="canonical" href="https://atolye.com/"></head>'
        "<body><h1>Ahşap Atölye</h1><p>"
        + "El yapımı ahşap mobilyalar ve özel tasarım " * 6
        + "</p></body></html>"
    )
    ctx = make_ctx(
        html=home, final_url="https://atolye.com/", pages=[("https://atolye.com/hakkimizda", home)]
    )
    (finding,) = await run(RouteServesRootShell, ctx)
    assert finding.severity == Severity.MEDIUM


async def test_root_shell_single_page_site_no_finding() -> None:
    assert (
        await run(RouteServesRootShell, single("https://tek.com/", "spa_prerendered_home.html"))
        == []
    )  # type: ignore[arg-type]


async def test_root_shell_pages_removed_from_other_checks() -> None:
    ctx = sohbetli_ctx()
    assert await run(DuplicateTitleDescription, ctx) == []  # type: ignore[arg-type]
    (og,) = await run(OgMissing, ctx)  # type: ignore[arg-type]
    assert f"{S}/turbo" not in [p["url"] for p in og.evidence["pages"]]
    assert og.evidence["covered_by"] == "web.render.route_serves_root_shell"
    for finding in await run(EmptyServerHtml, ctx):  # type: ignore[arg-type]
        assert f"{S}/turbo" not in [p["url"] for p in finding.evidence["pages"]]


# --- D. web.privacy.internal_role_exposed


async def test_roles_next_data_with_email_is_high() -> None:
    (finding,) = await run(InternalRoleExposed, sohbetli_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH
    ev = finding.evidence
    assert ev["pages"] == [f"{S}/directory/profiles"]
    assert ev["role_labels"] == ["SUPER_ADMIN"]
    assert ev["json_fields"] == ["role", "email"]
    assert ev["sensitive_user_fields_in_json"] is True
    dumped = json.dumps(ev)
    assert "kurucu" not in dumped and "@" not in dumped  # kullanıcı adı ve değerler yok
    rendered(finding.check_id, ev)


async def test_roles_wordpress_visible_badge_medium() -> None:
    (finding,) = await run(
        InternalRoleExposed, single("https://forum.com/uyeler/", "wp_author_admin_badge.html")
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["role_labels"] == ["ADMIN"]  # "Moderatör" etiketi sayılmaz


async def test_roles_plain_initial_state_admin_flag_medium() -> None:
    (finding,) = await run(
        InternalRoleExposed, single("https://pano.com/", "plain_initial_state.html")
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["json_fields"] == ["isAdmin"]


async def test_roles_docs_code_examples_and_user_facing_badges_no_finding() -> None:
    # <pre><code> içindeki ADMIN, "SYSTEM REQUIREMENTS" başlığı, nav'daki ADMIN linki,
    # "Yönetici"/"Moderatör" rozetleri, ld+json ve role="button" UI yapılandırması
    assert (
        await run(
            InternalRoleExposed, single("https://docs.acme.com/roles", "docs_code_example.html")
        )
        == []
    )  # type: ignore[arg-type]


# --- E. web.links.dead_or_inconsistent_nav


async def test_nav_dead_contact_and_missing_footer_links_medium() -> None:
    (finding,) = await run(DeadOrInconsistentNav, sohbetli_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM and finding.category == Category.CONVERSION
    ev = finding.evidence
    assert ev["dead_items"] == [{"page": f"{S}/", "text": "İletişim", "location": "footer"}]
    assert ev["missing_on_homepage"] == ["Çerez Politikası", "Topluluk Kuralları"]
    rendered(finding.check_id, ev)


async def test_nav_wordpress_dead_support_link_medium_dropdown_and_column_title_ok() -> None:
    (finding,) = await run(
        DeadOrInconsistentNav, single("https://kafe.com/", "wp_footer_dead_support.html")
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    texts = [d["text"] for d in finding.evidence["dead_items"]]
    assert texts == ["Destek"]  # "Menü" açılır menü tetikleyicisi, "Kurumsal" sütun başlığı


async def test_nav_plain_html_text_item_among_links_low() -> None:
    (finding,) = await run(
        DeadOrInconsistentNav, single("https://atolye.com/", "plain_nav_plain_item.html")
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    assert [d["text"] for d in finding.evidence["dead_items"]] == ["Blog"]  # telif satırı sayılmaz


async def test_nav_dropdown_trigger_no_finding() -> None:
    page = (
        '<html><body><nav><a href="#" aria-haspopup="true">Ürünler</a><a href="/a">A</a></nav>'
        '<footer><a href="/gizlilik">Gizlilik</a><a href="/iletisim">İletişim</a></footer></body></html>'
    )
    assert await run(DeadOrInconsistentNav, make_ctx(html=page, final_url="https://s.com/")) == []


async def test_nav_same_footer_everywhere_no_finding() -> None:
    page = html("spa_prerendered_home.html")
    ctx = make_ctx(
        html=page,
        final_url="https://taskly.app/",
        pages=[("https://taskly.app/agencies", html("spa_landing_variant.html"))],
    )
    assert await run(DeadOrInconsistentNav, ctx) == []


# --- F. web.conversion.cta_targets_login


async def test_cta_header_login_only_and_last_cta_login_medium() -> None:
    (finding,) = await run(CtaTargetsLogin, sohbetli_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    ev = finding.evidence
    assert ev["header_login"] is True and ev["header_register"] is False
    assert ev["last_section_cta"] == {"text": "Uygulamayı Aç", "target": "login"}
    assert ev["reasons"] == ["header_login_only", "last_cta_login"]
    assert (
        rendered(finding.check_id, ev)
        == "Ana sayfan yeni ziyaretçiyi kayda değil girişe yönlendiriyor"
    )


async def test_cta_plain_html_majority_login() -> None:
    (finding,) = await run(
        CtaTargetsLogin, single("https://faturapro.com/", "plain_login_ctas.html")
    )  # type: ignore[arg-type]
    assert "most_ctas_login" in finding.evidence["reasons"]
    assert finding.evidence["cta_counts"] == {"login": 3, "register": 1}


async def test_cta_wordpress_no_register_anywhere_is_info() -> None:
    (finding,) = await run(CtaTargetsLogin, single("https://kulup.com/", "wp_login_only.html"))  # type: ignore[arg-type]
    assert finding.severity == Severity.INFO
    assert finding.evidence["no_register_path"] is True
    assert (
        rendered(finding.check_id, finding.evidence, "en")
        == "There's no visible way to sign up on your home page"
    )


async def test_cta_header_has_both_no_finding() -> None:
    assert (
        await run(CtaTargetsLogin, single("https://taskly.app/", "spa_prerendered_home.html")) == []
    )  # type: ignore[arg-type]


async def test_cta_last_section_goes_to_register_no_finding() -> None:
    page = (
        html("next_community_home.html")
        .replace(
            '<nav><a href="/">Sohbetli</a>',
            '<nav><a href="/">Sohbetli</a><a class="btn" href="/register">Kayıt ol</a>',
        )
        .replace('href="/login">Uygulamayı Aç', 'href="/register">Ücretsiz başla')
    )
    assert await run(CtaTargetsLogin, make_ctx(html=page, final_url=f"{S}/")) == []


@pytest.mark.parametrize(
    ("url", "target"),
    [
        ("https://s.com/giris", "login"),
        ("https://s.com/auth?mode=login", "login"),
        ("https://s.com/uye-ol", "register"),
        ("https://s.com/auth?mode=signup", "register"),
        ("https://s.com/blog/login-sorunlari", None),
    ],
)
def test_auth_target(url: str, target: str | None) -> None:
    assert auth_target(url) == target


# --- G. web.conversion.empty_public_listing


async def test_listing_single_profile_medium_with_counter() -> None:
    (finding,) = await run(EmptyPublicListing, sohbetli_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    (page,) = finding.evidence["pages"]
    assert page["url"] == f"{S}/directory/profiles" and page["item_count"] == 1
    assert page["visible_counters"] == ["1 üye"]
    rendered(finding.check_id, finding.evidence)


async def test_listing_spa_three_servers_and_small_counters() -> None:
    (finding,) = await run(
        EmptyPublicListing, single("https://chatterbox.gg/explore", "spa_explore_servers.html")
    )  # type: ignore[arg-type]
    (page,) = finding.evidence["pages"]
    assert page["item_count"] == 3
    assert "12 members" in page["visible_counters"]


async def test_listing_wordpress_empty_reviews() -> None:
    (finding,) = await run(
        EmptyPublicListing, single("https://kuafor.com/yorumlar/", "wp_reviews_empty.html")
    )  # type: ignore[arg-type]
    assert finding.evidence["pages"][0]["empty_state_text"] is True


async def test_listing_fifty_items_no_finding() -> None:
    items = "".join(f'<li class="member"><a href="/u/{i}">Üye {i}</a></li>' for i in range(50))
    page = f"<html><body><h1>Üyeler</h1><p>1.250 üye</p><ul>{items}</ul></body></html>"
    assert (
        await run(EmptyPublicListing, make_ctx(html=page, final_url="https://s.com/uyeler")) == []
    )


async def test_listing_noindex_members_only_no_finding() -> None:
    page = (
        '<html><head><meta name="robots" content="noindex"></head><body><h1>Üyeler</h1>'
        '<p>Bu sayfayı görmek için giriş yap.</p><li class="member"><a href="/u/1">A</a></li></body></html>'
    )
    assert (
        await run(EmptyPublicListing, make_ctx(html=page, final_url="https://s.com/uyeler")) == []
    )


def test_visible_counters_keep_original_text() -> None:
    assert visible_counters("Şu anda 1 üye; 3 adımda kur; 12 members") == [
        (1, "1 üye"),
        (12, "12 members"),
    ]


# --- H. web.conversion.keyword_stuffing


async def test_stuffing_faq_phrase_and_meta_keywords_low() -> None:
    (finding,) = await run(KeywordStuffing, sohbetli_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    (page,) = finding.evidence["pages"]  # /turbo ana sayfa kopyası: ayrıca sayılmaz
    assert (
        page["phrase"] == "turkiyenin yeni sohbet uygulamasi" and page["count"] == 5
    )  # h1 kısa blok
    assert finding.evidence["meta_keywords_count"] == 12
    rendered(finding.check_id, finding.evidence)


async def test_stuffing_wordpress_local_seo_phrase() -> None:
    (finding,) = await run(
        KeywordStuffing, single("https://gulusdis.com/", "wp_clinic_stuffing.html")
    )  # type: ignore[arg-type]
    assert finding.evidence["pages"][0]["phrase"] == "istanbul en iyi dis klinigi"


async def test_stuffing_natural_brand_repetition_no_finding() -> None:
    # Marka adı her cümlede geçiyor ama tekrar eden bir öbek yok
    text = (
        "Acme ile faturalarını dakikalar içinde kesersin. Stok takibini Acme panelinden yaparsın. "
        "Müşterilerin Acme üzerinden ödeme linki alır. Raporlar her sabah e-postana gelir. "
        "Ekibin vardiya planını tek ekranda görür. Teklifleri şablonla hazırlayıp gönderirsin. "
        "Tahsilatlar banka hesabınla otomatik eşleşir. E-arşiv faturaları yasal süre boyunca "
        "saklanır. Masraf fişlerini telefonla fotoğraflayıp yüklersin. Bordro hesapları her ay "
        "güncel mevzuata göre yapılır. Acme mobil uygulaması iOS ve Android'de çalışır. Destek "
        "ekibimize hafta içi telefonla, hafta sonu canlı yazışmayla ulaşabilirsin. Verilerin "
        "Türkiye'deki veri merkezlerinde şifreli tutulur. Acme'yi otuz gün ücretsiz dene; kredi "
        "kartı istemiyoruz. Muhasebecinle panelini paylaşıp onun da hesaplara bakmasını "
        "sağlayabilirsin. Yıllık planda iki ay bizden."
    )
    page = (
        '<html><head><meta property="og:site_name" content="Acme"></head>'
        f"<body><main><p>{text}</p></main></body></html>"
    )
    assert await run(KeywordStuffing, make_ctx(html=page, final_url="https://acme.com.tr/")) == []


async def test_stuffing_long_natural_blog_no_finding() -> None:
    sentences = [
        "Ekşi maya ekmeği yapmak sabır ister ama karşılığını fazlasıyla verir",
        "İlk gün un ve suyu karıştırıp kavanozu ılık bir yerde bekletin",
        "Her sabah yarısını atıp taze un ve su ekleyerek mayayı besleyin",
        "Bir hafta sonra maya kabarcıklı ve hafif ekşi kokulu hâle gelir",
        "Hamuru yoğururken suyu azar azar eklemek gluten ağını güçlendirir",
        "Fırını en az yarım saat önceden ısıtmak kabuğun çıtır olmasını sağlar",
        "Pişen ekmeği kesmeden önce bir saat soğumaya bırakın ki içi otursun",
        "Artan dilimleri dondurarak haftalarca taze tutabilirsiniz",
        "Tam buğday unu daha çok su çeker, oranı buna göre artırın",
        "Çavdar eklemek ekmeğe koyu bir renk ve belirgin bir aroma katar",
        "Hamuru katlama tekniğiyle her yarım saatte bir gerin ve katlayın",
        "Mayalanma süresi mutfağın sıcaklığına göre dört ile on iki saat arasında değişir",
        "Döküm tencerede pişirmek ev fırınında buhar etkisi yaratmanın en kolay yoludur",
        "Kapağı ilk yirmi dakika kapalı tutun, sonra açıp kabuğu kızartın",
        "Ekmeğin pişip pişmediğini altına vurduğunuzda çıkan boş sesle anlarsınız",
        "Tuzu karıştırmadan önce otolizi yarım saat beklemek hamuru yumuşatır",
        "Mayanız uyuşuk kalırsa beslemeyi günde iki kez yapmayı deneyin",
        "Buzdolabında tutulan maya haftada bir beslenirse uzun süre canlı kalır",
        "Ilık su kullanmak soğuk mutfaklarda mayalanmayı hızlandırır",
        "Kesim atmak ekmeğin fırında nereden açılacağını kontrol etmenizi sağlar",
    ]
    page = (
        "<html><body><article>"
        + "".join(f"<p>{x}.</p>" for x in sentences)
        + "</article></body></html>"
    )
    assert await run(KeywordStuffing, make_ctx(html=page, final_url="https://ekmek.blog/")) == []


async def test_stuffing_repeated_card_template_text_no_finding() -> None:
    # Dizin kartlarındaki şablon açıklama ve görsel altyazıları doldurma değildir
    cards = "".join(
        f'<div class="server-card"><h3>Sunucu {i}</h3><p class="card-desc">Platform üzerinde '
        f"sesli ve yazılı iletişim sağlayan topluluk sunucusu</p><span>{i} üye</span></div>"
        for i in range(20)
    )
    captions = "".join(
        f"<figure><img src='/{i}.jpg' alt=''><figcaption>Gerçek arayüz · örnek içerik</figcaption></figure>"
        for i in range(5)
    )
    page = f"<html><body><main><h1>Sunucular</h1>{cards}{captions}</main></body></html>"
    assert (
        await run(KeywordStuffing, make_ctx(html=page, final_url="https://s.com/sunucular")) == []
    )


async def test_nav_page_like_button_among_footer_links_is_dead() -> None:
    page = (
        '<html><body><main><h1>X</h1></main><footer><nav><a href="/privacy">Gizlilik</a>'
        '<a href="/terms">Kullanım</a><button type="button">İletişim</button>'
        '<button type="button">Çerez tercihleri</button></nav></footer></body></html>'
    )
    (finding,) = await run(DeadOrInconsistentNav, make_ctx(html=page, final_url="https://s.com/"))
    assert finding.severity == Severity.MEDIUM
    assert [d["text"] for d in finding.evidence["dead_items"]] == [
        "İletişim"
    ]  # çerez düğmesi meşru
    assert finding.evidence["dead_items"][0]["location"] == "footer"  # footer > nav


# --- I. web.conversion.value_proposition (Faz 2 iskeleti)


def test_value_proposition_registered_for_phase_2_only() -> None:
    reg = load_builtin_checks()
    assert reg.get("web.conversion.value_proposition") is not None
    assert "web.conversion.value_proposition" not in {
        c.id for c in reg.for_target(TargetType.WEB, 1)
    }
    assert "web.conversion.value_proposition" in {c.id for c in reg.for_target(TargetType.WEB, 2)}


async def test_value_proposition_without_llm_client_produces_nothing() -> None:
    assert await ValueProposition().run(sohbetli_ctx()) == []  # type: ignore[arg-type]


def test_value_proposition_input_and_prompt() -> None:
    ctx = sohbetli_ctx()
    ctx.meta["has_login"] = True  # type: ignore[attr-defined]
    data = build_input(ctx)  # type: ignore[arg-type]
    assert data.title.startswith("Sohbetli")
    assert "discord alternatifi" in data.meta_keywords
    assert data.headings[0] == "Türkiye'nin yeni sohbet uygulaması"
    assert any(c.text == "Hemen Başla" and c.target.endswith("/register") for c in data.ctas)
    assert "Giriş Yap" in data.nav_links and data.flags == {"has_login": True}
    prompt = build_prompt(data, "tr")
    assert GUARDRAILS in prompt and "positioning_hidden_in_meta" in prompt
    assert len(RUBRIC) == 7


def test_value_proposition_output_validation_drops_invented_evidence() -> None:
    data = build_input(sohbetli_ctx())  # type: ignore[arg-type]
    scores = {item.value: {"score": 1, "reason": "r", "suggestion": "s"} for item in RubricItem}
    raw = json.dumps(
        {
            "scores": scores,
            "findings": [
                {
                    "rubric_item": "why",
                    "severity": "medium",
                    "evidence_text": "discord alternatifi",
                    "suggestion": "Farkı görünür metinde anlat.",
                    "positioning_hidden_in_meta": True,
                },
                {
                    "rubric_item": "trust",
                    "severity": "low",
                    "evidence_text": "10.000 mutlu kullanıcı",
                    "suggestion": "uydurma",
                },
            ],
        }
    )
    output = parse_output(raw, data)
    assert isinstance(output, ValuePropositionOutput)
    assert [f.evidence_text for f in output.findings] == ["discord alternatifi"]
    assert parse_output('{"scores": {"what": {"score": 5}}}', data) is None


# --- ortak: kayıt, metinler, ağırlıklar


@pytest.mark.parametrize("check_id", NEW_IDS)
def test_new_checks_registered_with_texts(check_id: str) -> None:
    assert load_builtin_checks().get(check_id) is not None
    for lang in ("tr", "en"):
        text = finding_text(check_id, lang, {})
        assert text.title and text.description and text.fix_steps and text.fix_prompt


def test_score_weights_include_conversion() -> None:
    assert get_settings().score_weights == {
        "security": 0.5,
        "prod_readiness": 0.25,
        "conversion": 0.25,
    }
