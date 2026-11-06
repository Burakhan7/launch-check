"""Paylaşım ve arama sonuçlarında görünen temel meta bilgileri."""

import re
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck, html_tree, is_html, meta_content, page_tree
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
        for index, page in enumerate(ctx.pages or []):
            missing, issues = og_problems(social_meta(page_tree(ctx, page)))
            if not missing and not issues:
                continue
            entries.append({"url": page.url, "missing": missing, "issues": issues})
            partial = len(missing) < len(OG_REQUIRED)
            if index == 0 and "og:image" in missing and partial:
                severity = Severity.MEDIUM
        if not entries:
            return []
        return [self.finding({"home_url": ctx.final_url, "pages": entries}, severity=severity)]

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
