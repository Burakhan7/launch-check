"""web.content.framework_leftovers: şablon sayfası, varsayılan başlık/açıklama/ikon, araç markası."""

import hashlib

import pytest

from launchcheck.checks.base import Severity
from launchcheck.checks.web import content
from launchcheck.checks.web.content import FrameworkLeftovers
from launchcheck.i18n import finding_text, fragment_fn
from tests.checks.helpers import SITE, fixture_html, make_ctx, resp, run


def leftovers(name: str) -> str:
    return fixture_html(f"leftovers/{name}")


def kinds(finding: object) -> list[str]:
    return [i["kind"] for i in finding.evidence["items"]]  # type: ignore[attr-defined]


async def test_vite_template_is_high_with_three_items() -> None:
    (finding,) = await run(FrameworkLeftovers, make_ctx(html=leftovers("vite_template.html")))
    assert finding.severity == Severity.HIGH
    assert kinds(finding) == ["template_page", "default_title", "default_favicon"]
    items = {i["kind"]: i for i in finding.evidence["items"]}
    assert items["default_title"] == {
        "kind": "default_title",
        "page": "/",
        "value": "Vite + React + TS",
    }
    assert items["default_favicon"]["value"] == "/vite.svg"
    assert "and save to test" in items["template_page"]["value"]
    assert "count is" in items["template_page"]["value"]  # Vite logosu var
    assert all(len(i["value"]) <= 120 for i in finding.evidence["items"])


async def test_lovable_meta_is_low() -> None:
    (finding,) = await run(FrameworkLeftovers, make_ctx(html=leftovers("lovable_meta.html")))
    assert finding.severity == Severity.LOW
    assert kinds(finding) == ["tool_branding_meta", "tool_og_image"]


async def test_clean_site_no_finding() -> None:
    assert await run(FrameworkLeftovers, make_ctx(html=leftovers("clean.html"))) == []


async def test_blog_about_vite_code_block_not_counted() -> None:
    assert await run(FrameworkLeftovers, make_ctx(html=leftovers("blog_about_vite.html"))) == []


@pytest.mark.parametrize(
    "site", ["https://vite.dev/", "https://www.lovable.dev/", "https://nextjs.org/"]
)
async def test_tool_own_site_is_skipped(site: str) -> None:
    ctx = make_ctx(html=leftovers("vite_template.html"), final_url=site)
    assert await run(FrameworkLeftovers, ctx) == []


async def test_count_is_needs_framework_logo() -> None:
    html = (
        "<html><head><title>Sayaç</title></head><body><main><p>count is 3</p></main></body></html>"
    )
    assert await run(FrameworkLeftovers, make_ctx(html=html)) == []


async def test_default_description_and_cra_title() -> None:
    html = (
        '<html><head><title>React App</title><meta name="description" '
        'content="Web site created using create-react-app"></head><body><p>x</p></body></html>'
    )
    (finding,) = await run(FrameworkLeftovers, make_ctx(html=html))
    assert finding.severity == Severity.MEDIUM
    assert kinds(finding) == ["default_title", "default_description"]


async def test_empty_spa_shell_scans_bundle_for_template_text() -> None:
    """Vite 9: sunucu HTML'i boş kabuk; şablon metni derlenmiş JSX'te bölünmüş hâlde."""
    html = (
        '<!doctype html><html><head><link rel="icon" href="/favicon.svg"><title>Vite + React + TS'
        '</title></head><body><div id="root"></div></body></html>'
    )
    js = '(0,a.jsxs)("p",{children:["Edit ",(0,a.jsx)("code",{children:"src/App.tsx"})," and save to test ",(0,a.jsx)("code",{children:"HMR"})]})'
    ctx = make_ctx(html=html, scripts={f"{SITE}/assets/index-abc.js": js})
    (finding,) = await run(FrameworkLeftovers, ctx)
    assert finding.severity == Severity.HIGH
    page_item = finding.evidence["items"][0]
    assert page_item == {
        "kind": "template_page",
        "page": "/",
        "value": "and save to test",
        "source": "js",
    }


async def test_bundle_not_scanned_when_page_has_content() -> None:
    html = leftovers("clean.html").replace("</main>", "<p>" + "kelime " * 50 + "</p></main>")
    js = '"Edit "," and save to test "'
    ctx = make_ctx(html=html, scripts={f"{SITE}/assets/a.js": js})
    assert await run(FrameworkLeftovers, ctx) == []


async def test_favicon_hash_match(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"\x00\x00\x01\x00fake-default-icon"
    monkeypatch.setitem(content.KNOWN_DEFAULT_FAVICON_HASHES, hashlib.sha256(body).hexdigest(), "t")
    routes = {
        f"{SITE}/favicon.ico": resp(f"{SITE}/favicon.ico", body=body, content_type="image/x-icon")
    }
    (finding,) = await run(
        FrameworkLeftovers, make_ctx(html=leftovers("clean.html"), routes=routes)
    )
    assert finding.evidence["items"] == [
        {"kind": "default_favicon", "page": "/", "value": "/favicon.ico"}
    ]
    assert finding.severity == Severity.LOW


async def test_favicon_hash_other_icon_no_finding() -> None:
    routes = {
        f"{SITE}/favicon.ico": resp(
            f"{SITE}/favicon.ico", body=b"\x00\x00\x01\x00own", content_type="image/x-icon"
        )
    }
    assert (
        await run(FrameworkLeftovers, make_ctx(html=leftovers("clean.html"), routes=routes)) == []
    )


async def test_favicon_dedup_across_pages_counts_pages() -> None:
    head = '<link rel="icon" href="/next.svg"><title>Acme</title>'
    page = f"<html><head>{head}</head><body><main><p>Merhaba</p></main></body></html>"
    other = page.replace("Merhaba", "Hakkımızda bilgi")
    ctx = make_ctx(html=page, pages=[(f"{SITE}/a", other), (f"{SITE}/b", other + " ")])
    (finding,) = await run(FrameworkLeftovers, ctx)
    assert finding.evidence["items"] == [
        {"kind": "default_favicon", "page": "/", "value": "/next.svg", "pages": 3}
    ]


async def test_own_lovable_app_og_image_is_not_tool_image() -> None:
    html = leftovers("clean.html").replace(
        "https://patikuafor.example.com/og.png", "https://pati.lovable.app/og.png"
    )
    ctx = make_ctx(html=html, final_url="https://pati.lovable.app/")
    assert await run(FrameworkLeftovers, ctx) == []


def test_text_params_lists_kind_names() -> None:
    check = FrameworkLeftovers()
    evidence = {"items": [{"kind": "default_title"}, {"kind": "default_favicon"}]}
    text = finding_text(check.id, "tr", check.text_params(evidence, fragment_fn(check.id, "tr")))
    assert "varsayılan sayfa başlığı, varsayılan site ikonu" in text.description


async def test_vite9_template_simulation() -> None:
    """create-vite@9.3.4 react-ts: gerçek index.html (build sonrası script yolu) + derlenmiş
    App bileşeninin biçimi. Spec'teki "gerçek doğrulama" beklentisinin çevrimdışı karşılığı."""
    from launchcheck.checks.web.meta import ViewportMissing

    js = (
        '(0,s.jsx)("h1",{children:"Get started"}),(0,s.jsxs)("p",{children:["Edit ",'
        '(0,s.jsx)("code",{children:"src/App.tsx"})," and save to test ",'
        '(0,s.jsx)("code",{children:"HMR"})]}),(0,s.jsxs)("button",{children:["Count is ",e]})'
    )
    ctx = make_ctx(
        html=leftovers("vite9_react_ts_index.html"),
        scripts={f"{SITE}/assets/index-abc.js": js},
    )
    (finding,) = await run(FrameworkLeftovers, ctx)
    assert finding.severity == Severity.HIGH
    assert kinds(finding) == ["template_page", "default_title"]
    assert await run(ViewportMissing, ctx) == []
