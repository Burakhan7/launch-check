"""Sayfalardaki linkler: şifresiz (http://) bağlantılar ve bozuk YouTube linkleri.
Pasif: link hedeflerine istek atılmaz (HTTPS desteği, videonun varlığı Faz 2 işi)."""

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.domains import same_site
from launchcheck.checks.web._common import WebCheck
from launchcheck.checks.web._html import attr, iter_pages, location_of
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

MAX_LINKS = 20
MAX_PAGES = 5
IGNORED_HTTP_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}  # noqa: S104 (sadece karşılaştırma)
IGNORED_HTTP_SUFFIXES = (
    "example.com",
    "example.org",
    "example.net",
    ".example",
    ".test",
    ".invalid",
    ".local",
)
LOCATION_RANK = {"nav": 0, "footer": 1, "content": 2}


def _ignored_host(host: str) -> bool:
    return host in IGNORED_HTTP_HOSTS or host.endswith(IGNORED_HTTP_SUFFIXES)


@register
class InsecureHttpLinks(WebCheck):
    id = "web.links.insecure_http_links"
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        site_host = (urlsplit(ctx.final_url).hostname or "").lower()
        links: dict[str, dict[str, Any]] = {}
        for page, tree in iter_pages(ctx):
            for node in tree.css("a[href]"):
                href = attr(node, "href").strip()
                if not href.lower().startswith("http://"):
                    continue
                url = href.split("#", 1)[0]
                host = (urlsplit(url).hostname or "").lower()
                if not host or _ignored_host(host):
                    continue
                own = same_site(host, site_host)
                if own and not ctx.is_https:
                    continue  # site hiç HTTPS değilse bunu web.https.missing raporlar
                entry = links.setdefault(
                    url, {"url": url, "pages": [], "location": "content", "same_site": own}
                )
                if page.url not in entry["pages"] and len(entry["pages"]) < MAX_PAGES:
                    entry["pages"].append(page.url)
                location = location_of(node)
                if LOCATION_RANK[location] < LOCATION_RANK[entry["location"]]:
                    entry["location"] = location
        if not links:
            return []

        def severity(link: dict[str, Any]) -> Severity:
            prominent = link["same_site"] or link["location"] in ("nav", "footer")
            return Severity.MEDIUM if prominent else Severity.LOW

        ordered = sorted(links.values(), key=lambda link: severity(link).rank)
        worst = severity(ordered[0])
        evidence = {"links": ordered[:MAX_LINKS], "count": len(ordered)}
        return [self.finding(evidence, severity=worst)]


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ENCODED_QUERY_RE = re.compile(r"%3f|%26|%3d|[?&=]", re.IGNORECASE)
ID_PATH_PREFIXES = ("/embed/", "/shorts/", "/live/", "/v/")
NON_VIDEO_EMBEDS = {"videoseries"}  # /embed/videoseries?list=... oynatma listesi
RAW_V_PARAM_RE = re.compile(r"(?:^|&)v=([^&]*)")


def _check_id(raw_id: str) -> str | None:
    """Ham (decode edilmemiş) video ID'sini doğrular; sorun yoksa None."""
    if ENCODED_QUERY_RE.search(raw_id):
        return "encoded_query_in_id"
    if not VIDEO_ID_RE.match(raw_id):
        return "invalid_length"
    return None


def youtube_problem(url: str) -> str | None:
    """YouTube video linki bozuksa sebebi; video linki değilse veya sağlamsa None."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    path = parts.path
    if host == "youtu.be":
        return _check_id(path.strip("/").split("/", 1)[0])
    if path.rstrip("/") == "/watch":
        match = RAW_V_PARAM_RE.search(parts.query)
        return _check_id(match.group(1)) if match else "missing_v"
    for prefix in ID_PATH_PREFIXES:
        if path.startswith(prefix):
            raw_id = path[len(prefix) :].split("/", 1)[0]
            return None if raw_id in NON_VIDEO_EMBEDS else _check_id(raw_id)
    return None  # kanal, oynatma listesi, arama vb.


@register
class MalformedYoutube(WebCheck):
    id = "web.links.malformed_youtube"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        broken: dict[str, dict[str, str]] = {}
        for page, tree in iter_pages(ctx):
            for node in tree.css("a[href], iframe[src]"):
                raw = attr(node, "href" if node.tag == "a" else "src").strip()
                if not raw:
                    continue
                url = urljoin(page.url, raw)
                reason = youtube_problem(url)
                if reason and url not in broken:
                    broken[url] = {"url": url, "page": page.url, "reason": reason}
        if not broken:
            return []
        threshold = get_settings().web_youtube_medium_threshold
        severity = Severity.MEDIUM if len(broken) >= threshold else Severity.LOW
        evidence = {"links": list(broken.values())[:MAX_LINKS], "count": len(broken)}
        return [self.finding(evidence, severity=severity)]
