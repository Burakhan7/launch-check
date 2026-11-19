"""Render check'leri: gerçek tarayıcıda görülen JS hataları, başarısız istekler, doğrudan açınca
404 veren SPA sayfaları, ekrana düşmüş teknik değerler, yüklenemeyen görseller."""

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.checks.web.deep._common import MAX_ITEMS, MAX_PAGES, RenderCheck, path_of
from launchcheck.config import get_settings
from launchcheck.deep.context import DeepContext
from launchcheck.deep.render_data import RenderData

NORMALIZE_RE = re.compile(r"https?://\S+|\b\d+\b")
API_TYPES = ("fetch", "xhr")


@register
class JsRuntimeErrors(RenderCheck):
    id = "web.render.js_runtime_errors"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        grouped: dict[str, dict[str, Any]] = {}
        important = False
        for index, page in enumerate(data.pages):
            for error in page.console_errors:
                key = NORMALIZE_RE.sub("#", error.message)
                entry = grouped.setdefault(
                    key, {"message": error.message, "kind": error.kind, "pages": []}
                )
                path = path_of(page.url)
                if path not in entry["pages"]:
                    entry["pages"].append(path)
                important = important or index == 0 or page.is_funnel
        if not grouped:
            return []
        errors = [
            {**e, "pages": e["pages"][:MAX_PAGES], "page_count": len(e["pages"])}
            for e in grouped.values()
        ]
        evidence = {"errors": errors[:MAX_ITEMS], "count": len(errors)}
        return [self.finding(evidence, Severity.HIGH if important else Severity.MEDIUM)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"count": str(evidence.get("count", 0))}


@register
class FailedRequests(RenderCheck):
    """Kendi alan adına ve sitenin API'lerine giden başarısız istekler. Görseller ayrı
    (broken_images), ölçüm/reklam servisleri ve bizim proxy'nin engelledikleri hariç."""

    id = "web.render.failed_requests"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        ignored = set(get_settings().deep_failed_request_ignore_domains)
        site = registered_domain(
            urlsplit(data.pages[0].final_url or data.pages[0].url).hostname or ""
        )
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        pages: dict[tuple[str, str], list[str]] = defaultdict(list)
        for page in data.pages:
            for failure in page.failures:
                if failure.resource_type == "image":
                    continue
                domain = registered_domain(urlsplit(failure.url).hostname or "")
                if domain in ignored:
                    continue
                if domain != site and failure.resource_type not in API_TYPES:
                    continue  # başka sitenin statik dosyası: sitenin API'si değil
                key = (failure.url, failure.error)
                grouped.setdefault(
                    key,
                    {
                        "url": failure.url,
                        "type": failure.resource_type,
                        "status": failure.status,
                        "error": failure.error,
                    },
                )
                if path_of(page.url) not in pages[key]:
                    pages[key].append(path_of(page.url))
        if not grouped:
            return []
        requests = [{**v, "pages": pages[k][:MAX_PAGES]} for k, v in grouped.items()]
        api_failed = any(r["type"] in API_TYPES for r in requests)
        evidence = {
            "requests": requests[:MAX_ITEMS],
            "count": len(requests),
            "api_failed": api_failed,
        }
        return [self.finding(evidence, Severity.HIGH if api_failed else Severity.MEDIUM)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        api = fragment("api_note") if evidence.get("api_failed") else ""
        return {"count": str(evidence.get("count", 0)), "api_note": api}


@register
class SpaDeepLink404(RenderCheck):
    id = "web.render.spa_deep_link_404"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        broken = [
            {"url": path_of(p.url), "direct_status": p.direct_status}
            for p in data.deep_links
            if p.direct_status == 404 and p.via_click_ok
        ]
        return [self.finding({"pages": broken[:MAX_ITEMS]})] if broken else []


JUNK_LABELS = {"undefined", "NaN", "null", "[object Object]", "Invalid Date"}


@register
class VisibleJunkText(RenderCheck):
    id = "web.render.visible_junk_text"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        items: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for page in data.pages:
            for junk in page.junk:
                key = (junk.token, junk.snippet)
                if key in seen:
                    continue
                seen.add(key)
                items.append({"token": junk.token, "text": junk.snippet, "page": path_of(page.url)})
        if not items:
            return []
        return [self.finding({"items": items[:MAX_ITEMS], "count": len(items)})]


@register
class BrokenImages(RenderCheck):
    id = "web.render.broken_images"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        items: dict[str, dict[str, Any]] = {}
        for page in data.pages:
            for image in page.broken_images:
                entry = items.setdefault(
                    image.src, {"src": image.src, "page": path_of(page.url), "hero": False}
                )
                entry["hero"] = entry["hero"] or image.hero
        if not items:
            return []
        hero = any(i["hero"] for i in items.values())
        evidence = {"images": list(items.values())[:MAX_ITEMS], "count": len(items)}
        return [self.finding(evidence, Severity.MEDIUM if hero else Severity.LOW)]
