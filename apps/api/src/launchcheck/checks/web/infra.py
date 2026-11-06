"""Hosting platformunun varsayılan alan adından (doğrudan uygulama sunucusundan) yüklenen
kaynaklar. Pasif: kaynaklara istek atılmaz, sadece HTML/JS içindeki adreslere bakılır."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.dev_urls import URL_HOST_RE
from launchcheck.checks.web._common import WebCheck, page_tree
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

MAX_SAMPLES = 5
CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")\s]+)['"]?\s*\)""", re.IGNORECASE)
RESOURCE_LINK_RELS = {
    "stylesheet",
    "preload",
    "modulepreload",
    "icon",
    "shortcut",
    "apple-touch-icon",
}
META_IMAGE_KEYS = ("og:image", "og:image:url", "og:image:secure_url", "twitter:image")
# Bu header'lardan biri varsa site bir CDN/proxy arkasındadır.
CDN_HEADERS = ("cf-ray", "x-vercel-cache", "x-served-by", "via", "x-amz-cf-id", "cf-cache-status")
CDN_SERVERS = ("cloudflare", "cloudfront", "akamaighost", "fastly")


def matches_platform(host: str, domains: list[str]) -> bool:
    """`*.vercel.app` gibi: sadece alt alan adları eşleşir (çıplak `vercel.app` linkleri değil)."""
    host = host.lower().rstrip(".")
    return any(host.endswith("." + d.lower().lstrip("*.")) for d in domains)


def is_behind_cdn(headers: dict[str, str]) -> bool:
    server = headers.get("server", "").lower()
    return any(h in headers for h in CDN_HEADERS) or any(s in server for s in CDN_SERVERS)


def _srcset_urls(value: str) -> list[str]:
    return [part.strip().split()[0] for part in value.split(",") if part.strip()]


def html_resource_urls(tree: LexborHTMLParser) -> Iterator[str]:
    """Sayfanın yüklediği kaynak adresleri (göreli olabilir)."""
    for node in tree.css("img, source, video, audio, script, link, meta, [style], style"):
        attrs = node.attributes
        tag = node.tag
        if tag in ("img", "source", "video", "audio", "script"):
            for key in ("src", "poster"):
                if attrs.get(key):
                    yield str(attrs[key])
            if attrs.get("srcset"):
                yield from _srcset_urls(str(attrs["srcset"]))
        elif tag == "link":
            rels = set((attrs.get("rel") or "").lower().split())
            if rels & RESOURCE_LINK_RELS and attrs.get("href"):
                yield str(attrs["href"])
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in META_IMAGE_KEYS and attrs.get("content"):
                yield str(attrs["content"])
        if tag == "style":
            yield from CSS_URL_RE.findall(node.text(deep=True))
        if attrs.get("style"):
            yield from CSS_URL_RE.findall(str(attrs["style"]))


@dataclass
class _HostHits:
    urls: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)

    def add(self, url: str, page: str) -> None:
        if url not in self.urls:
            self.urls.append(url)
        if page not in self.pages:
            self.pages.append(page)


@register
class OriginExposed(WebCheck):
    id = "web.infra.origin_exposed"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        domains = get_settings().web_platform_default_domains
        own_host = (urlsplit(ctx.final_url).hostname or "").lower()
        if matches_platform(own_host, domains):
            return []  # site zaten platform alan adında yayında: beklenen durum
        hits: dict[str, _HostHits] = {}

        def consider(raw: str, base: str, page: str) -> None:
            url = urljoin(base, raw.strip())
            host = (urlsplit(url).hostname or "").lower()
            if host and host != own_host and matches_platform(host, domains):
                hits.setdefault(host, _HostHits()).add(url.split("#", 1)[0], page)

        for page in ctx.pages:
            for raw in html_resource_urls(page_tree(ctx, page)):
                consider(raw, page.url, page.url)
        # JS bundle'ları ana sayfadan yüklenir; içlerindeki mutlak adresler de sayılır.
        for script in ctx.scripts:
            for m in URL_HOST_RE.finditer(script.content):
                consider(m.group(0), ctx.final_url, ctx.final_url)

        behind_cdn = is_behind_cdn(ctx.headers)
        severity = Severity.HIGH if behind_cdn else Severity.MEDIUM
        return [
            self.finding(
                {
                    "host": host,
                    "resource_count": len(h.urls),
                    "sample_urls": h.urls[:MAX_SAMPLES],
                    "pages": h.pages[:MAX_SAMPLES],
                    "behind_cdn": behind_cdn,
                },
                severity=severity,
            )
            for host, h in hits.items()
        ]
