"""Paylaşım ve arama sonuçlarında görünen temel meta bilgileri."""

import re
from typing import Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck, html_tree, is_html, meta_content, page_tree
from launchcheck.checks.web._shell import covered_urls
from launchcheck.checks.web.render import covered_note
from launchcheck.collectors.web_collector import WebContext

ICON_RELS = ("icon", "shortcut icon", "apple-touch-icon")
SITEMAP_RE = re.compile(rb"<(urlset|sitemapindex)\b", re.IGNORECASE)
ROBOTS_SITEMAP_RE = re.compile(rb"(?im)^\s*sitemap\s*:\s*\S+")


@register
class TitleDescriptionMissing(WebCheck):
    id = "web.meta.title_description_missing"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        tree = html_tree(ctx)
        title = tree.css_first("title")
        missing = []
        if title is None or not title.text(deep=True).strip():
            missing.append("title")
        if not meta_content(tree, name="description"):
            missing.append("description")
        return [self.finding({"missing": missing})] if missing else []


OG_REQUIRED = ("og:title", "og:description", "og:image")
# og:url kontrol edilir ama "eksik" sayılmaz: yoksa platformlar paylaşılan sayfa adresini kullanır.
OG_IMAGE_RATIO = 1.91  # 1200x630
OG_IMAGE_RATIO_TOLERANCE = 0.15
ISSUE_NOT_ABSOLUTE = "og_image_not_absolute"
ISSUE_TWITTER_NO_IMAGE = "twitter_card_without_image"
ISSUE_ASPECT_RATIO = "og_image_aspect_ratio"


def social_meta(tree: LexborHTMLParser) -> dict[str, str]:
    """og:* ve twitter:* etiketleri. Bazı framework'ler og: etiketlerini `name` ile yazar;
    hem `property` hem `name` okunur."""
    values: dict[str, str] = {}
    for node in tree.css("meta"):
        attrs = node.attributes
        key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        content = (attrs.get("content") or "").strip()
        if key.startswith(("og:", "twitter:")) and content and key not in values:
            values[key] = content
    return values


def _ratio_off(meta: dict[str, str]) -> bool:
    try:
        width = float(meta["og:image:width"])
        height = float(meta["og:image:height"])
    except (KeyError, ValueError):
        return False
    if width <= 0 or height <= 0:
        return False
    return abs(width / height - OG_IMAGE_RATIO) / OG_IMAGE_RATIO > OG_IMAGE_RATIO_TOLERANCE


def og_problems(meta: dict[str, str]) -> tuple[list[str], list[str]]:
    missing = [tag for tag in OG_REQUIRED if tag not in meta]
    issues = []
    image = meta.get("og:image")
    if image and not image.lower().startswith("https://"):
        issues.append(ISSUE_NOT_ABSOLUTE)
    card = meta.get("twitter:card", "").lower()
    if card == "summary_large_image" and not image and not meta.get("twitter:image"):
        issues.append(ISSUE_TWITTER_NO_IMAGE)
    if image and _ratio_off(meta):
        issues.append(ISSUE_ASPECT_RATIO)
    return missing, issues


@register
class OgMissing(WebCheck):
    """Her sayfada og:title, og:description, og:image ayrı ayrı kontrol edilir; sadece gerçekten
    eksik olanlar raporlanır. Ana sayfada og:image eksikse (diğerleri varken) önem medium olur."""

    id = "web.meta.og_missing"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        entries = []
        severity = Severity.LOW
        covered = covered_urls(ctx)  # ana sayfa kopyası dönen sayfalar ayrı raporlanır
        for index, page in enumerate(ctx.pages or []):
            if page.url in covered:
                continue
            missing, issues = og_problems(social_meta(page_tree(ctx, page)))
            if not missing and not issues:
                continue
            entries.append({"url": page.url, "missing": missing, "issues": issues})
            partial = len(missing) < len(OG_REQUIRED)
            if index == 0 and "og:image" in missing and partial:
                severity = Severity.MEDIUM
        if not entries:
            return []
        evidence = {"home_url": ctx.final_url, "pages": entries, **covered_note(covered)}
        return [self.finding(evidence, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        pages: list[dict[str, Any]] = evidence.get("pages") or []
        home_url = evidence.get("home_url")
        home_affected = any(p.get("url") == home_url for p in pages)
        others = len(pages) - (1 if home_affected else 0)
        if home_affected and others:
            where = fragment("where_home_and_pages", count=others)
        elif home_affected:
            where = fragment("where_home")
        else:
            where = fragment("where_pages", count=others)

        tags = list(dict.fromkeys(t for p in pages for t in p.get("missing") or []))
        issues = list(dict.fromkeys(i for p in pages for i in p.get("issues") or []))
        issue_texts = [fragment(f"issue_{i}") for i in issues]
        headline = (
            fragment("headline_missing", where=where, tags=", ".join(tags))
            if tags
            else fragment("headline_issues", where=where)
        )
        details = "; ".join(
            fragment(
                "page_line",
                url=p.get("url", ""),
                items=", ".join(
                    [fragment("missing_item", tag=t) for t in p.get("missing") or []]
                    + [fragment(f"issue_{i}") for i in p.get("issues") or []]
                ),
            )
            for p in pages
        )
        todo = ", ".join([*tags, *issue_texts])
        return {"headline": headline, "details": details, "tags": ", ".join(tags), "todo": todo}


VIEWPORT_ISSUES = ("missing", "fixed_width", "no_device_width", "zoom_blocked")
VIEWPORT_MAX_PAGES = 10
VIEWPORT_MAX_CONTENT_CHARS = 200
VIEWPORT_MIN_MAX_SCALE = 2.0  # bunun altı yakınlaştırmayı fiilen engeller
NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?(?:px)?$")


def parse_viewport(content: str) -> dict[str, str]:
    """`width = Device-Width, initial-scale=1` → {"width": "device-width", ...}"""
    out: dict[str, str] = {}
    for part in content.split(","):
        key, sep, value = part.partition("=")
        if sep:
            out.setdefault(key.strip().lower(), value.strip().lower())
    return out


def viewport_issues(content: str | None) -> list[str]:
    if content is None:
        return ["missing"]
    props = parse_viewport(content)
    issues = []
    width = props.get("width")
    if width is not None and NUMBER_RE.match(width):
        issues.append("fixed_width")
    elif width != "device-width":
        issues.append("no_device_width")
    try:
        max_scale = float(props["maximum-scale"])
    except (KeyError, ValueError):
        max_scale = None
    if props.get("user-scalable") in ("no", "0") or (
        max_scale is not None and max_scale < VIEWPORT_MIN_MAX_SCALE
    ):
        issues.append("zoom_blocked")
    return issues


def _viewport_content(tree: LexborHTMLParser) -> str | None:
    for node in tree.css("meta[name]"):
        if (node.attributes.get("name") or "").strip().lower() == "viewport":
            return node.attributes.get("content") or ""
    return None


def _path(url: str) -> str:
    return urlsplit(url).path or "/"


@register
class ViewportMissing(WebCheck):
    """Telefonda düzgün görünmeyi sağlayan viewport etiketi: yok, sabit genişlik, cihaz
    genişliği kullanılmıyor veya yakınlaştırma kapatılmış."""

    id = "web.meta.viewport_missing"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        covered = covered_urls(ctx)
        pages = [p for p in ctx.pages or [] if p.url not in covered]
        by_issue: dict[str, list[str]] = {}
        contents: list[str] = []
        flagged: set[str] = set()
        home_missing = False
        for index, page in enumerate(pages):
            content = _viewport_content(page_tree(ctx, page))
            issues = viewport_issues(content)
            if not issues:
                continue
            flagged.add(page.url)
            home_missing = home_missing or (index == 0 and "missing" in issues)
            if content:
                contents.append(content)
            for issue in issues:
                by_issue.setdefault(issue, []).append(_path(page.url))
        if not by_issue:
            return []
        evidence = self._evidence(by_issue, contents, home_missing, len(flagged) == len(pages))
        return [
            self.finding(
                {**evidence, **covered_note(covered)}, self._severity(by_issue, home_missing)
            )
        ]

    @staticmethod
    def _severity(by_issue: dict[str, list[str]], home_missing: bool) -> Severity:
        if home_missing or "fixed_width" in by_issue:
            return Severity.MEDIUM
        return Severity.LOW

    @staticmethod
    def _evidence(
        by_issue: dict[str, list[str]], contents: list[str], home_missing: bool, all_pages: bool
    ) -> dict[str, Any]:
        issues = [i for i in VIEWPORT_ISSUES if i in by_issue]
        if home_missing:
            # Ana sayfada etiket yoksa iç sayfaları tek tek listelemeye gerek yok.
            pages = ["/"]
        else:
            pages = list(dict.fromkeys(p for i in issues for p in by_issue[i]))
        page_count = len({p for i in issues for p in by_issue[i]})
        evidence: dict[str, Any] = {
            "issues": issues,
            "pages": pages[:VIEWPORT_MAX_PAGES],
            "page_count": page_count,
            "site_wide": all_pages,
            "by_issue": [
                {
                    "issue": i,
                    "pages": by_issue[i][:VIEWPORT_MAX_PAGES],
                    "page_count": len(by_issue[i]),
                }
                for i in issues
            ],
        }
        if contents:
            evidence["content"] = contents[0][:VIEWPORT_MAX_CONTENT_CHARS]
        return evidence

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        issues = [str(i) for i in evidence.get("issues") or []]
        return {"issues": ", ".join(fragment(i) for i in issues)}


@register
class FaviconMissing(WebCheck):
    id = "web.meta.favicon_missing"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        for node in html_tree(ctx).css("link[rel]"):
            if (node.attributes.get("rel") or "").lower().strip() in ICON_RELS:
                return []
        res = await ctx.real_file("/favicon.ico")
        if res is not None and not is_html(res):
            return []
        return [self.finding({})]


@register
class SitemapMissing(WebCheck):
    id = "web.meta.sitemap_missing"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        sitemap = await ctx.real_file("/sitemap.xml")
        if sitemap is not None and SITEMAP_RE.search(sitemap.body[:4096]):
            return []
        robots = await ctx.real_file("/robots.txt")
        if robots is not None and not is_html(robots) and ROBOTS_SITEMAP_RE.search(robots.body):
            return []
        return [self.finding({"path": "/sitemap.xml"})]
