"""Bundle'da unutulmuş geliştirme/test adresleri."""

import re
from urllib.parse import urlsplit

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import MAX_EVIDENCE_ITEMS, WebCheck, js_sources
from launchcheck.collectors.web_collector import WebContext

# Port veya yol içermeli: çıplak "http://localhost" kütüphanelerde sık geçer (yanlış alarm).
LOCALHOST_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.0\.2\.2)"
    r"(?::\d{2,5}(?:/[^\s\"'`<>)\\]*)?|/[^\s\"'`<>)\\]+)"
)
# Kütüphanelerin varsayılan yerel adresleri (uygulama kodu değil): OpenTelemetry OTLP 4317/4318
LIBRARY_DEFAULT_LOCAL_RE = re.compile(r"^https?://(?:localhost|127\.0\.0\.1):431[78](?:/|$)")
URL_HOST_RE = re.compile(
    r"https?://([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::\d+)?(?:/[^\s\"'`<>)\\]*)?", re.I
)
NON_PROD_LABELS = {"staging", "stage", "stg", "dev", "develop", "development", "test", "qa", "uat"}
NON_PROD_SUFFIXES = ("-dev", "-staging", "-stg", "-test", "-qa", "-uat")
NON_PROD_TLDS = ("test", "local", "localhost", "internal")
MIN_LABELS_FOR_SUBDOMAIN = 3
# Bilinen, "dev."/"test." ile başlayan ama üretim olan üçüncü taraf alan adları
KNOWN_SAFE_HOSTS = {"dev.to", "test.com", "dev.mysql.com", "dev.w3.org", "test.example.com"}


def _unique_matches(ctx: WebContext, pattern: re.Pattern[str]) -> dict[str, list[str]]:
    """eşleşme → bulunduğu dosyalar"""
    found: dict[str, list[str]] = {}
    for name, text in js_sources(ctx):
        for m in pattern.finditer(text):
            files = found.setdefault(m.group(0).rstrip(".,;"), [])
            if name not in files:
                files.append(name)
    return found


def _evidence(found: dict[str, list[str]]) -> dict[str, object]:
    urls = list(found)[:MAX_EVIDENCE_ITEMS]
    files = list(dict.fromkeys(f for u in urls for f in found[u]))[:MAX_EVIDENCE_ITEMS]
    return {"urls": urls, "files": files, "count": len(found)}


@register
class LocalhostUrls(WebCheck):
    id = "web.bundle.localhost_urls"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        found = {
            url: files
            for url, files in _unique_matches(ctx, LOCALHOST_URL_RE).items()
            if not LIBRARY_DEFAULT_LOCAL_RE.match(url)
        }
        return [self.finding(_evidence(found))] if found else []


def is_non_prod_host(host: str) -> bool:
    host = host.lower()
    if host in KNOWN_SAFE_HOSTS:
        return False
    labels = host.split(".")
    first = labels[0]
    # "staging.com" gibi iki parçalı host'lar alan adının kendisidir, alt alan adı değil.
    has_subdomain = len(labels) >= MIN_LABELS_FOR_SUBDOMAIN
    return (
        (has_subdomain and (first in NON_PROD_LABELS or first.endswith(NON_PROD_SUFFIXES)))
        or any(lbl in ("staging", "stg") for lbl in labels[1:-2])
        or labels[-1] in NON_PROD_TLDS
    )


@register
class StagingUrls(WebCheck):
    id = "web.bundle.staging_urls"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        own_host = (urlsplit(ctx.final_url).hostname or "").lower()
        found = {
            url: files
            for url, files in _unique_matches(ctx, URL_HOST_RE).items()
            if (host := (urlsplit(url).hostname or "")) != own_host and is_non_prod_host(host)
        }
        return [self.finding(_evidence(found))] if found else []
