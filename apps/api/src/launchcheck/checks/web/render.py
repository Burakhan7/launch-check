"""Sunucudan gelen HTML'de ana içerik var mı? (tarayıcı kullanmadan, sadece ham HTML)

İçerik sadece tarayıcıda JavaScript ile yükleniyorsa arama motorları ve sosyal medya
botları sayfayı boş veya eksik görür.
"""

import re
import statistics
from dataclasses import dataclass
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck, is_noindex, page_tree
from launchcheck.collectors.web_collector import PageSnapshot, WebContext
from launchcheck.config import get_settings

INVISIBLE_SELECTORS = ("script", "style", "noscript", "template", "svg")
CHROME_SELECTORS = (
    "header",
    "nav",
    "footer",
    "aside",
    '[role="navigation"]',
    '[role="banner"]',
    '[role="contentinfo"]',
)
SPA_ROOT_IDS = {"root", "app", "__next", "__nuxt", "svelte", "q-app"}
# Boş kabuk tespitinde body'nin doğrudan çocukları arasında yok sayılan etiketler
SHELL_IGNORED_TAGS = {
    "script",
    "noscript",
    "style",
    "link",
    "template",
    "meta",
    "-text",
    "-comment",
    "_comment",
    "!comment",
}
WORD_RE = re.compile(r"\w+", re.UNICODE)
MIN_REFERENCE_PAGES = 3


@dataclass(frozen=True)
class PageContent:
    url: str
    words: int  # ana içerik kelime sayısı (h1 dahil)
    words_without_h1: int
    empty_shell: bool


def _count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def is_empty_shell(tree: LexborHTMLParser) -> bool:
    """body'de sadece boş bir kök element (#root, #app, #__next...) ve script'ler var mı?"""
    body = tree.body
    if body is None:
        return False
    elements = [
        child for child in body.iter(include_text=False) if child.tag not in SHELL_IGNORED_TAGS
    ]
    if not elements:
        return True
    if len(elements) != 1:
        return False
    root = elements[0]
    has_children = any(c.tag not in SHELL_IGNORED_TAGS for c in root.iter(include_text=False))
    return (
        root.tag == "div"
        and (root.attributes.get("id") or "") in SPA_ROOT_IDS
        and not has_children
        and not root.text(deep=True).strip()
    )


def measure(page: PageSnapshot) -> PageContent:
    # Ağaç değiştirildiği için önbellekteki ağaç değil, sayfanın kendi kopyası kullanılır.
    tree = LexborHTMLParser(page.html)
    shell = is_empty_shell(tree)
    for selector in (*INVISIBLE_SELECTORS, *CHROME_SELECTORS):
        for node in tree.css(selector):
            node.decompose()
    body = tree.body
    text = body.text(deep=True, separator=" ") if body is not None else ""
    h1_words = sum(_count_words(n.text(deep=True, separator=" ")) for n in tree.css("h1"))
    words = _count_words(text)
    return PageContent(page.url, words, words - h1_words, shell)


def _skipped_path(url: str, skip_paths: list[str]) -> bool:
    segments = {s.lower() for s in urlsplit(url).path.split("/") if s}
    return any(p.strip("/").lower() in segments for p in skip_paths)


@register
class EmptyServerHtml(WebCheck):
    id = "web.render.empty_server_html"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        settings = get_settings()
        threshold = settings.web_min_main_content_words
        measured = [
            measure(page)
            for page in ctx.pages
            if not _skipped_path(page.url, settings.web_render_skip_paths)
            and not is_noindex(page, page_tree(ctx, page))
        ]
        flagged = [p for p in measured if p.empty_shell or p.words_without_h1 < threshold]
        if not flagged:
            return []
        healthy = [p.words for p in measured if p not in flagged]
        reference = int(statistics.median(healthy)) if len(healthy) >= MIN_REFERENCE_PAGES else 0
        severity = Severity.HIGH if any(p.empty_shell for p in flagged) else Severity.MEDIUM
        return [
            self.finding(
                {
                    "pages": [
                        {"url": p.url, "main_content_words": p.words, "empty_shell": p.empty_shell}
                        for p in flagged
                    ],
                    "reference_median_words": reference,
                },
                severity=severity,
            )
        ]
