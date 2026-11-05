"""Paylaşım ve arama sonuçlarında görünen temel meta bilgileri."""

import re

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck, html_tree, is_html, meta_content
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


@register
class OgMissing(WebCheck):
    id = "web.meta.og_missing"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        tree = html_tree(ctx)
        missing = [
            p for p in ("og:title", "og:description", "og:image") if not meta_content(tree, prop=p)
        ]
        return [self.finding({"missing": missing})] if missing else []


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
