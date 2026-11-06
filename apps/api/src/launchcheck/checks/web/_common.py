"""Web check'lerinin ortak tabanı ve yardımcıları."""

from abc import abstractmethod
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Check, Finding, ScanContext, TargetType
from launchcheck.checks.shared.secret_patterns import SecretHit, find_secrets, find_supabase_jwts
from launchcheck.collectors.fetcher import FetchResult
from launchcheck.collectors.web_collector import PageSnapshot, WebContext

MAX_EVIDENCE_ITEMS = 10


class WebCheck(Check):
    target = TargetType.WEB

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, WebContext):
            raise TypeError("WebContext bekleniyordu")
        return await self.check(ctx)

    @abstractmethod
    async def check(self, ctx: WebContext) -> list[Finding]: ...


def html_tree(ctx: WebContext) -> LexborHTMLParser:
    if "html_tree" not in ctx.cache:
        ctx.cache["html_tree"] = LexborHTMLParser(ctx.html)
    tree: LexborHTMLParser = ctx.cache["html_tree"]
    return tree


def js_sources(ctx: WebContext) -> list[tuple[str, str]]:
    """Taranacak JS kaynakları: (konum, içerik). Inline script'ler dahil."""
    return [(s.url, s.content) for s in ctx.scripts]


def external_js_sources(ctx: WebContext) -> list[tuple[str, str]]:
    return [(s.url, s.content) for s in ctx.scripts if not s.inline]


def cached_secrets(ctx: WebContext) -> list[SecretHit]:
    if "secrets" not in ctx.cache:
        ctx.cache["secrets"] = find_secrets(js_sources(ctx))
    hits: list[SecretHit] = ctx.cache["secrets"]
    return hits


def cached_jwts(ctx: WebContext) -> list[SecretHit]:
    if "jwts" not in ctx.cache:
        ctx.cache["jwts"] = find_supabase_jwts(js_sources(ctx))
    hits: list[SecretHit] = ctx.cache["jwts"]
    return hits


def is_html(res: FetchResult) -> bool:
    if "html" in res.content_type:
        return True
    head = res.body[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html"))


def meta_content(
    tree: LexborHTMLParser, *, name: str | None = None, prop: str | None = None
) -> str:
    for node in tree.css("meta"):
        attrs: dict[str, Any] = node.attributes
        if name and (attrs.get("name") or "").lower() == name:
            return (attrs.get("content") or "").strip()
        if prop and (attrs.get("property") or "").lower() == prop:
            return (attrs.get("content") or "").strip()
    return ""


def page_tree(ctx: WebContext, page: PageSnapshot) -> LexborHTMLParser:
    """Sayfa başına önbellekli HTML ağacı. Değiştirmek isteyen check kendi kopyasını açmalı."""
    key = f"page_tree:{page.url}"
    if key not in ctx.cache:
        ctx.cache[key] = LexborHTMLParser(page.html)
    tree: LexborHTMLParser = ctx.cache[key]
    return tree


def is_noindex(page: PageSnapshot, tree: LexborHTMLParser) -> bool:
    if "noindex" in page.headers.get("x-robots-tag", "").lower():
        return True
    for node in tree.css("meta[name]"):
        name = (node.attributes.get("name") or "").lower()
        if (
            name in ("robots", "googlebot")
            and "noindex" in (node.attributes.get("content") or "").lower()
        ):
            return True
    return False
