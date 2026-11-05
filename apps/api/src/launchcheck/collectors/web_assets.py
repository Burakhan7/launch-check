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
