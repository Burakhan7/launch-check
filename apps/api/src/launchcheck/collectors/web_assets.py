"""HTML ve JS içinden script/chunk referanslarını çıkaran saf fonksiyonlar (ağ erişimi yok)."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

# Tırnak içindeki .js/.mjs yolları; en az bir "/" içermeli (rastgele "react.js" string'leri elenir).
_CHUNK_RE = re.compile(
    r"""["'`]((?:\.{1,2}/|/)?(?:[A-Za-z0-9_\-.~@]+/)+[A-Za-z0-9_\-.~@]+\.m?js)(?:\?[^"'`\s]*)?["'`]"""
)
_SCRIPT_PRELOAD_RELS = ("modulepreload", "preload")


@dataclass(frozen=True)
class HtmlScripts:
    external: list[str]  # aynı origin'den, mutlak URL
    inline: list[str]
    all_external: list[str]  # origin farketmeksizin (platform tespiti, mixed content için)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def is_same_origin(url: str, origin: str) -> bool:
    return origin_of(url) == origin.lower()


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def extract_html_scripts(html: str, base_url: str) -> HtmlScripts:
    tree = LexborHTMLParser(html)
    origin = origin_of(base_url)
    refs: list[str] = []
    inline: list[str] = []
    for node in tree.css("script"):
        src = node.attributes.get("src")
        if src:
            refs.append(urljoin(base_url, src.strip()))
        else:
            text = node.text(deep=True, separator="")
            if text.strip():
                inline.append(text)
    for node in tree.css("link[href]"):
        rel = (node.attributes.get("rel") or "").lower().split()
        as_attr = (node.attributes.get("as") or "").lower()
        if "modulepreload" in rel or ("preload" in rel and as_attr == "script"):
            refs.append(urljoin(base_url, (node.attributes.get("href") or "").strip()))
    refs = [r for r in _dedupe(refs) if r.startswith(("http://", "https://"))]
    return HtmlScripts(
        external=[r for r in refs if is_same_origin(r, origin)],
        inline=inline,
        all_external=refs,
    )


SKIPPED_LINK_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "sms:", "whatsapp:")
SKIPPED_LINK_EXTENSIONS = frozenset(
    [
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".avif",
        ".svg",
        ".ico",
        ".bmp",
        ".zip",
        ".rar",
        ".7z",
        ".gz",
        ".tar",
        ".mp4",
        ".webm",
        ".mov",
        ".avi",
        ".mp3",
        ".wav",
        ".ogg",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".csv",
        ".json",
        ".xml",
        ".txt",
        ".apk",
        ".dmg",
        ".exe",
        ".css",
        ".js",
        ".mjs",
        ".map",
        ".woff",
        ".woff2",
        ".ttf",
        ".rss",
    ]
)
# Önce site iskeletindeki (menü, başlık, alt bilgi) linkler: genelde ana sayfalara gider.
PRIORITY_LINK_SELECTORS = ("nav a[href]", "header a[href]", "footer a[href]")


def _normalize_link(href: str, base_url: str, origin: str) -> str | None:
    href = href.strip()
    if not href or href.startswith("#") or href.lower().startswith(SKIPPED_LINK_SCHEMES):
        return None
    parts = urlsplit(urljoin(base_url, href))
    if parts.scheme not in ("http", "https"):
        return None
    url = f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"  # query ve fragment atılır
    if not is_same_origin(url, origin):
        return None
    last_segment = parts.path.rsplit("/", 1)[-1].lower()
    if "." in last_segment and "." + last_segment.rsplit(".", 1)[-1] in SKIPPED_LINK_EXTENSIONS:
        return None
    return url


def _link_key(url: str) -> str:
    return url.rstrip("/") or url


def split_internal_links(
    html: str, base_url: str, exclude: tuple[str, ...] = ()
) -> tuple[list[str], list[str]]:
    """Aynı origin iç sayfa linkleri iki grup hâlinde: (nav/header/footer içindekiler, diğerleri).
    Tekilleştirilir; query/fragment, dosya linkleri ve mailto/tel gibi şemalar atılır."""
    tree = LexborHTMLParser(html)
    origin = origin_of(base_url)
    seen = {_link_key(u) for u in exclude}
    groups: tuple[list[str], list[str]] = ([], [])
    for selector in (*PRIORITY_LINK_SELECTORS, "a[href]"):
        group = groups[0] if selector in PRIORITY_LINK_SELECTORS else groups[1]
        for node in tree.css(selector):
            url = _normalize_link(node.attributes.get("href") or "", base_url, origin)
            if url is not None and _link_key(url) not in seen:
                seen.add(_link_key(url))
                group.append(url)
    return groups


def extract_internal_links(html: str, base_url: str, exclude: tuple[str, ...] = ()) -> list[str]:
    """Aynı origin iç sayfa linkleri: önce nav/header/footer içindekiler, sonra diğerleri."""
    priority, content = split_internal_links(html, base_url, exclude)
    return priority + content


def pick_crawl_links(
    priority: list[str], content: list[str], limit: int, content_slots: int
) -> list[str]:
    """Önce menü linkleri, ama en az `content_slots` yer sayfa içi linklere (gönderi, ürün
    detayı gibi) ayrılır; içerik linki azsa kalan yer menü linkleriyle dolar."""
    reserved = min(content_slots, len(content), limit)
    head = priority[: limit - reserved]
    return head + content[: limit - len(head)]


def _resolve_chunk(path: str, js_url: str, origin: str) -> str | None:
    if path.startswith("/"):
        return origin + path
    if path.startswith(("./", "../")):
        return urljoin(js_url, path)
    if path.startswith(("assets/", "_next/")):
        return f"{origin}/{path}"
    if path.startswith("static/"):  # Next.js build manifest: "static/chunks/..."
        return f"{origin}/_next/{path}"
    return None


def extract_chunk_refs(js: str, js_url: str, origin: str) -> list[str]:
    """JS içindeki lazy chunk yollarını (Vite `assets/...`, Next `static/chunks/...`) bulur."""
    found = []
    for match in _CHUNK_RE.finditer(js):
        url = _resolve_chunk(match.group(1), js_url, origin)
        if url and is_same_origin(url, origin):
            found.append(url)
    return _dedupe(found)
