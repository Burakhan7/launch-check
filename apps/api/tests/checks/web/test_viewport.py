"""web.meta.viewport_missing: eksik, sabit genişlik, zoom engeli, doğru etiket, yazım varyasyonları."""

import pytest

from launchcheck.checks.base import Severity
from launchcheck.checks.web.meta import ViewportMissing, parse_viewport, viewport_issues
from tests.checks.helpers import SITE, make_ctx, run

GOOD = '<meta name="viewport" content="width=device-width, initial-scale=1">'


def page(head: str = GOOD, body: str = "<p>Merhaba</p>") -> str:
    return f"<html><head><title>T</title>{head}</head><body>{body}</body></html>"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, ["missing"]),
        ("width=device-width, initial-scale=1", []),
        ("Width = Device-Width , Initial-Scale = 1", []),
        ("width=1024", ["fixed_width"]),
        ("initial-scale=1", ["no_device_width"]),
        (
            "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no",
            ["zoom_blocked"],
        ),
        ("width=device-width, user-scalable=0", ["zoom_blocked"]),
        ("width=device-width, maximum-scale=1.5", ["zoom_blocked"]),
        ("width=device-width, maximum-scale=5", []),
    ],
)
def test_viewport_issues(content: str | None, expected: list[str]) -> None:
    assert viewport_issues(content) == expected


def test_parse_viewport_normalizes_keys_and_spaces() -> None:
    assert parse_viewport(" Width = Device-Width ,INITIAL-SCALE=1") == {
        "width": "device-width",
        "initial-scale": "1",
    }


async def test_correct_tag_no_finding() -> None:
    ctx = make_ctx(html=page(), pages=[(f"{SITE}/about", page())])
    assert await run(ViewportMissing, ctx) == []


async def test_case_and_space_variations_accepted() -> None:
    head = '<META NAME="Viewport" CONTENT="Width = Device-Width, initial-scale=1">'
    assert await run(ViewportMissing, make_ctx(html=page(head))) == []


async def test_missing_on_home_is_medium_and_site_wide() -> None:
    ctx = make_ctx(
        html=page(head=""), pages=[(f"{SITE}/a", page(head="")), (f"{SITE}/b", page(""))]
    )
    (finding,) = await run(ViewportMissing, ctx)
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["issues"] == ["missing"]
    assert finding.evidence["pages"] == ["/"]  # iç sayfalar ayrıca listelenmez
    assert finding.evidence["page_count"] == 3
    assert finding.evidence["site_wide"] is True


async def test_missing_only_on_inner_page_is_low() -> None:
    ctx = make_ctx(html=page(), pages=[(f"{SITE}/a", page(head="")), (f"{SITE}/b", page())])
    (finding,) = await run(ViewportMissing, ctx)
    assert finding.severity == Severity.LOW
    assert finding.evidence["pages"] == ["/a"]
    assert finding.evidence["site_wide"] is False


async def test_fixed_width_is_medium() -> None:
    head = '<meta name="viewport" content="width=1024">'
    (finding,) = await run(ViewportMissing, make_ctx(html=page(head)))
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["issues"] == ["fixed_width"]
    assert finding.evidence["content"] == "width=1024"


async def test_zoom_blocked_low_and_content_truncated() -> None:
    content = "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" + " " * 300
    head = f'<meta name="viewport" content="{content}">'
    ctx = make_ctx(html=page(head), pages=[(f"{SITE}/a", page(head))])
    (finding,) = await run(ViewportMissing, ctx)
    assert finding.severity == Severity.LOW
    assert finding.evidence["issues"] == ["zoom_blocked"]
    assert finding.evidence["pages"] == ["/", "/a"]
    assert finding.evidence["site_wide"] is True
    assert len(finding.evidence["content"]) == 200


def test_text_params() -> None:
    from launchcheck.i18n import finding_text, fragment_fn

    check = ViewportMissing()
    evidence = {"issues": ["missing", "zoom_blocked"]}
    params = check.text_params(evidence, fragment_fn(check.id, "tr"))
    text = finding_text(check.id, "tr", params)
    assert "etiket yok, yakınlaştırma kapatılmış" in text.description
