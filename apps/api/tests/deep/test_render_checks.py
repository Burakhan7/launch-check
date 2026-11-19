"""Render modülü check'leri: sentetik RenderData ile (tarayıcı gerekmez).

Her check için pozitif + en az 2 yanlış pozitif senaryosu; değer sızdırmama kontrolleri.
"""

import json
from typing import Any

import pytest

from launchcheck.checks.base import Check, Finding, Severity, TargetType
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.checks.web.deep.mobile import (
    CtaBelowFold,
    HorizontalOverflow,
    OverlappingElements,
    TapTargetsSmall,
)
from launchcheck.checks.web.deep.render import (
    BrokenImages,
    FailedRequests,
    JsRuntimeErrors,
    SpaDeepLink404,
    VisibleJunkText,
)
from launchcheck.checks.web.deep.security import (
    ClientOnlyAdminRoute,
    InsecureCookieFlags,
    SourcemapTreeExposed,
    TokenInWebStorage,
)
from launchcheck.checks.web.deep.ux import Custom404Missing, OfflineBreaksPage
from launchcheck.deep.context import DeepContext
from launchcheck.deep.render_data import (
    AdminProbe,
    Box,
    BrokenImage,
    ConsoleError,
    CookieInfo,
    DeepLinkProbe,
    JunkText,
    NetworkFailure,
    NotFoundProbe,
    OfflineProbe,
    RenderData,
    RenderedPage,
    StorageEntry,
    ViewportMetrics,
)
from launchcheck.i18n import finding_text, fragment_fn

SITE = "https://shop.example.com"


def metrics(width: int = 375, height: int = 812, **kw: Any) -> ViewportMetrics:
    return ViewportMetrics(
        width=width,
        height=height,
        inner_width=kw.pop("inner_width", width),
        scroll_width=kw.pop("scroll_width", width),
        **kw,
    )


def page(path: str = "/", **kw: Any) -> RenderedPage:
    mobile = kw.pop("mobile", metrics())
    desktop = kw.pop("desktop", metrics(1440, 900))
    p = RenderedPage(
        url=SITE + path,
        final_url=SITE + path,
        status=kw.pop("status", 200),
        mobile=mobile,
        desktop=desktop,
        **kw,
    )
    p.widths = kw.get("widths") or {mobile.width: mobile, desktop.width: desktop}
    return p


def ctx(*pages: RenderedPage, **kw: Any) -> DeepContext:
    data = RenderData(pages=list(pages) or [page()], **kw)
    return DeepContext(target_type=TargetType.WEB, target_label=SITE, render=data)


async def run(check: type[Check], c: DeepContext) -> list[Finding]:
    return await check().run(c)


def box(text: str = "Buton", **kw: Any) -> Box:
    return Box(
        tag=kw.get("tag", "a"),
        text=text,
        x=kw.get("x", 0),
        y=kw.get("y", 0),
        w=kw.get("w", 100),
        h=kw.get("h", 40),
        selector=kw.get("selector", "a.btn"),
    )


# --- genel


def test_all_render_checks_are_phase2_and_not_in_phase1() -> None:
    registry = load_builtin_checks()
    deep_ids = {c.id for c in registry.all() if c.module == "render"}
    assert len(deep_ids) == 15
    assert all(registry.get(i).phase == 2 for i in deep_ids)  # type: ignore[union-attr]
    phase1 = {c.id for c in registry.for_target(TargetType.WEB, 1)}
    assert not deep_ids & phase1


async def test_checks_without_render_data_return_nothing() -> None:
    empty = DeepContext(target_type=TargetType.WEB, target_label=SITE)
    for check in (JsRuntimeErrors, FailedRequests, TokenInWebStorage, HorizontalOverflow):
        assert await run(check, empty) == []


async def test_render_checks_reject_phase1_context() -> None:
    from tests.checks.helpers import make_ctx

    with pytest.raises(TypeError):
        await JsRuntimeErrors().run(make_ctx())


# --- js_runtime_errors


async def test_js_errors_grouped_and_high_on_home() -> None:
    err = ConsoleError(
        "TypeError: Cannot read properties of undefined (reading 'map') at 12", "exception"
    )
    same = ConsoleError(
        "TypeError: Cannot read properties of undefined (reading 'map') at 99", "exception"
    )
    (f,) = await run(
        JsRuntimeErrors, ctx(page(console_errors=[err]), page("/a", console_errors=[same]))
    )
    assert f.severity == Severity.HIGH
    assert f.evidence["count"] == 1 and f.evidence["errors"][0]["pages"] == ["/", "/a"]


async def test_js_errors_inner_page_medium_funnel_high() -> None:
    err = [ConsoleError("ReferenceError: x is not defined", "exception")]
    (f,) = await run(JsRuntimeErrors, ctx(page(), page("/blog", console_errors=err)))
    assert f.severity == Severity.MEDIUM
    (f,) = await run(
        JsRuntimeErrors, ctx(page(), page("/pricing", console_errors=err, is_funnel=True))
    )
    assert f.severity == Severity.HIGH


async def test_js_errors_none() -> None:
    assert await run(JsRuntimeErrors, ctx(page(), page("/a"))) == []


# --- failed_requests


async def test_failed_api_request_is_high() -> None:
    failures = [
        NetworkFailure("https://abc.supabase.co/rest/v1/items", "fetch", 500, "http_5xx"),
        NetworkFailure(SITE + "/assets/app.css", "stylesheet", 404, "http_4xx"),
    ]
    (f,) = await run(FailedRequests, ctx(page(failures=failures)))
    assert f.severity == Severity.HIGH and f.evidence["api_failed"] is True
    assert {r["url"] for r in f.evidence["requests"]} == {failures[0].url, failures[1].url}


async def test_failed_static_own_origin_is_medium() -> None:
    failures = [NetworkFailure(SITE + "/fonts/a.woff2", "font", 404, "http_4xx")]
    (f,) = await run(FailedRequests, ctx(page(failures=failures)))
    assert f.severity == Severity.MEDIUM


async def test_failed_requests_false_positives() -> None:
    failures = [
        NetworkFailure("https://www.google-analytics.com/g/collect", "fetch", None, "network"),
        NetworkFailure(
            "https://cdn.other-site.com/lib.js", "script", 404, "http_4xx"
        ),  # başka site
        NetworkFailure(SITE + "/img/hero.png", "image", 404, "http_4xx"),  # broken_images'ın işi
    ]
    assert await run(FailedRequests, ctx(page(failures=failures))) == []


# --- spa_deep_link_404


async def test_spa_deep_link_404() -> None:
    probes = [
        DeepLinkProbe(SITE + "/pricing", 404, True),
        DeepLinkProbe(SITE + "/gone", 404, False),
    ]
    (f,) = await run(SpaDeepLink404, ctx(deep_links=probes))
    assert f.evidence["pages"] == [{"url": "/pricing", "direct_status": 404}]


async def test_spa_deep_link_really_missing_or_ok() -> None:
    probes = [DeepLinkProbe(SITE + "/gone", 404, False), DeepLinkProbe(SITE + "/ok", 200, True)]
    assert await run(SpaDeepLink404, ctx(deep_links=probes)) == []


# --- visible_junk_text


async def test_junk_text_deduped() -> None:
    junk = [JunkText("undefined", "Fiyat: undefined ₺"), JunkText("NaN", "NaN yorum")]
    (f,) = await run(VisibleJunkText, ctx(page(junk=junk), page("/b", junk=junk[:1])))
    assert f.evidence["count"] == 2


async def test_junk_text_none() -> None:
    assert await run(VisibleJunkText, ctx(page(), page("/a"))) == []


# --- broken_images


async def test_broken_hero_image_medium_else_low() -> None:
    (f,) = await run(BrokenImages, ctx(page(broken_images=[BrokenImage("/hero.jpg", True)])))
    assert f.severity == Severity.MEDIUM
    (f,) = await run(BrokenImages, ctx(page(broken_images=[BrokenImage("/footer.png", False)])))
    assert f.severity == Severity.LOW


async def test_broken_images_none() -> None:
    assert await run(BrokenImages, ctx()) == []


# --- horizontal_overflow


async def test_overflow_reported_with_elements() -> None:
    narrow = metrics(
        320,
        568,
        scroll_width=410,
        overflowing=[box("Tablo", tag="table", w=410, selector="table.prices")],
    )
    p = page(widths={320: narrow, 375: metrics(), 1440: metrics(1440, 900)})
    (f,) = await run(HorizontalOverflow, ctx(p))
    (entry,) = f.evidence["pages"]
    assert (entry["width"], entry["overflow_px"]) == (320, 90)
    assert entry["elements"][0]["element"] == "table.prices"


async def test_overflow_false_positives() -> None:
    # 1-3 px yuvarlama farkı; viewport etiketi yok (sayfa 980 px'te çiziliyor: viewport_missing)
    rounding = page(widths={375: metrics(scroll_width=378)})
    no_viewport = page("/b", widths={375: metrics(inner_width=980, scroll_width=980)})
    desktop_only = page("/c", widths={1440: metrics(1440, 900, scroll_width=2000)})
    assert await run(HorizontalOverflow, ctx(rounding, no_viewport, desktop_only)) == []


# --- overlapping_elements


async def test_overlaps() -> None:
    m = metrics(overlaps=[(box("Kayıt ol"), box("Fiyatlar", x=10))])
    (f,) = await run(OverlappingElements, ctx(page(mobile=m, widths={375: m})))
    assert f.evidence["overlaps"][0]["width"] == 375


async def test_overlaps_none_and_deduped() -> None:
    assert await run(OverlappingElements, ctx()) == []
    m = metrics(overlaps=[(box("A"), box("B"))])
    (f,) = await run(OverlappingElements, ctx(page(widths={375: m}), page("/x", widths={375: m})))
    assert f.evidence["count"] == 1  # her sayfada tekrarlanan aynı öğe çifti bir kez sayılır


# --- cta_below_fold


async def test_cta_below_fold() -> None:
    m = metrics(primary_cta=box("Hemen başla", y=1300))
    (f,) = await run(CtaBelowFold, ctx(page(mobile=m)))
    assert f.evidence["cta_text"] == "Hemen başla" and f.evidence["screens_down"] == 1.6
    text = finding_text(
        CtaBelowFold.id,
        "tr",
        CtaBelowFold().text_params(f.evidence, fragment_fn(CtaBelowFold.id, "tr")),
    )
    assert "Hemen başla" in text.description


async def test_cta_above_fold_or_unknown() -> None:
    assert await run(CtaBelowFold, ctx(page(mobile=metrics(primary_cta=box(y=500))))) == []
    assert await run(CtaBelowFold, ctx(page(mobile=metrics(primary_cta=None)))) == []


# --- tap_targets_small


async def test_small_tap_targets() -> None:
    targets = [box(f"i{n}", w=16, h=16) for n in range(4)]
    (f,) = await run(TapTargetsSmall, ctx(page(mobile=metrics(small_targets=targets))))
    assert f.evidence["pages"][0]["count"] == 4


async def test_few_small_targets_not_reported() -> None:
    targets = [box("x", w=16, h=16), box("y", w=20, h=20)]
    assert await run(TapTargetsSmall, ctx(page(mobile=metrics(small_targets=targets)))) == []


# --- custom_404_missing


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        (NotFoundProbe("/x.txt", 404, 3, False, "nginx"), True),
        (NotFoundProbe("/x.txt", 404, 2, False, None), True),
        (NotFoundProbe("/x.txt", 404, 40, True, None), False),  # kendi 404 sayfası
        (NotFoundProbe("/x.txt", 200, 300, True, None), False),  # SPA ana sayfayı gösteriyor
        (NotFoundProbe("/x.txt", 404, 4, True, None), False),  # kısa ama ana sayfa linki var
    ],
)
async def test_custom_404(probe: NotFoundProbe, expected: bool) -> None:
    assert bool(await run(Custom404Missing, ctx(not_found=probe))) is expected


# --- offline_breaks_page


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("blank", True),
        ("spinner", True),
        ("message", False),
        ("browser_error_page", False),
        ("not_spa", False),
    ],
)
async def test_offline(outcome: str, expected: bool) -> None:
    probe = OfflineProbe(SITE + "/pricing", outcome, 0)
    assert bool(await run(OfflineBreaksPage, ctx(offline=probe))) is expected


# --- token_in_web_storage


async def test_long_lived_jwt_is_high_and_values_absent() -> None:
    storage = [
        StorageEntry("local", "sb-abcd-auth-token", 1200, True, 60.0),
        StorageEntry("local", "theme", 4, False, None),
    ]
    (f,) = await run(TokenInWebStorage, ctx(storage=storage))
    assert f.severity == Severity.HIGH
    assert f.evidence["items"] == [
        {"area": "local", "key": "sb-abcd-auth-token", "jwt": True, "expires_in_days": 60.0}
    ]


async def test_short_lived_token_medium() -> None:
    (f,) = await run(
        TokenInWebStorage, ctx(storage=[StorageEntry("session", "access_token", 900, True, 0.04)])
    )
    assert f.severity == Severity.MEDIUM


async def test_storage_false_positives() -> None:
    storage = [
        StorageEntry("local", "cart", 300, False, None),
        StorageEntry("local", "auth_redirect", 8, False, None),  # kısa değer, token değil
        StorageEntry("local", "i18nextLng", 2, False, None),
    ]
    assert await run(TokenInWebStorage, ctx(storage=storage)) == []


# --- insecure_cookie_flags


async def test_session_cookie_flags() -> None:
    cookies = [
        CookieInfo("connect.sid", "shop.example.com", False, False, "None", True),
        CookieInfo("_ga", "example.com", False, False, "Lax", False),
    ]
    (f,) = await run(InsecureCookieFlags, ctx(cookies=cookies))
    assert f.evidence["cookies"] == [
        {
            "name": "connect.sid",
            "issues": ["missing_secure", "missing_httponly", "samesite_none_insecure"],
        }
    ]
    text = finding_text(
        InsecureCookieFlags.id,
        "en",
        InsecureCookieFlags().text_params(f.evidence, fragment_fn(InsecureCookieFlags.id, "en")),
    )
    assert "HttpOnly missing" in text.description


async def test_cookie_false_positives() -> None:
    cookies = [
        CookieInfo("session", "shop.example.com", True, True, "Lax", True),  # doğru ayarlı
        CookieInfo("_gid", "example.com", False, False, "Lax", False),  # ölçüm
        CookieInfo("theme", "shop.example.com", False, False, "Lax", False),  # oturum değil
    ]
    assert await run(InsecureCookieFlags, ctx(cookies=cookies)) == []


# --- client_only_admin_route


async def test_admin_route_confirmed() -> None:
    probes = [
        AdminProbe(
            "/admin", 200, ["admin_heading", "data_table", "destructive_buttons"], False, False
        )
    ]
    (f,) = await run(ClientOnlyAdminRoute, ctx(admin=probes))
    assert f.severity == Severity.HIGH and f.evidence["verified"] is True


async def test_admin_route_unsure_is_info() -> None:
    (f,) = await run(
        ClientOnlyAdminRoute,
        ctx(admin=[AdminProbe("/panel", 200, ["admin_heading"], False, False)]),
    )
    assert f.severity == Severity.INFO and f.evidence["needs_phase3"] is True


async def test_admin_route_protected() -> None:
    probes = [
        AdminProbe("/admin", 200, ["admin_heading", "data_table"], True, False),  # giriş formu
        AdminProbe(
            "/admin/users", 200, ["admin_heading", "data_table"], False, True
        ),  # login'e yönlendi
        AdminProbe("/yonetim", 404, ["admin_heading", "data_table"], False, False),
        AdminProbe("/dashboard/admin", 200, [], False, False),
    ]
    assert await run(ClientOnlyAdminRoute, ctx(admin=probes)) == []


# --- sourcemap_tree_exposed


async def test_sourcemap_tree() -> None:
    (f,) = await run(
        SourcemapTreeExposed,
        ctx(sourcemap_sources=["src/App.tsx", "src/lib/supabase.ts"], sourcemap_source_count=2),
    )
    assert f.evidence == {"paths": ["src/App.tsx", "src/lib/supabase.ts"], "path_count": 2}
    assert await run(SourcemapTreeExposed, ctx()) == []


def test_no_storage_or_cookie_values_in_evidence_types() -> None:
    """StorageEntry/CookieInfo değer alanı taşımaz (yapı gereği)."""
    assert "value" not in StorageEntry.__dataclass_fields__
    assert "value" not in CookieInfo.__dataclass_fields__
    assert json.dumps({"ok": True})
