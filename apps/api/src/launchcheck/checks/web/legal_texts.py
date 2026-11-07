"""Yasal ve genel metin kalitesi: doldurulmamış şablon yer tutucuları ve KVKK yurt dışı aktarım
dayanağı. Pasif: taranan sayfalar + footer'daki en fazla birkaç yasal sayfa okunur.

Bu check'ler hukuki görüş değildir; metinler kullanıcıyı bir uzmana yönlendirir.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold, squash
from launchcheck.checks.web._common import WebCheck, page_tree
from launchcheck.checks.web._html import attr, content_signals, extra_info_pages, is_legal_page
from launchcheck.collectors.web_collector import PageSnapshot, WebContext

MAX_MATCHES = 10
CONTEXT_CHARS = 40
MAX_MATCH_CHARS = 80
# Bu öğelerin içi metin değil kod/girdi sayılır
CODE_TAGS = ("code", "pre", "kbd", "samp", "script", "style", "textarea", "template", "noscript")
# İstemci tarafı şablon motoru izleri: bu sayfalarda `{{ }}` tarayıcıda doldurulur.
CLIENT_TEMPLATE_RE = re.compile(r"\s(?:v-[a-z]+|:[a-z-]+|@[a-z]+|ng-[a-z]+|x-data|data-ng-)[=\s>]")
BRACKET_RE = re.compile(r"\[([^\[\]\n]{2,60})\](?!\()")  # "[metin](url)" markdown linki değil
MUSTACHE_RE = re.compile(r"\{\{\s*[^{}\n]{1,40}\}\}")
ANGLE_RE = re.compile(r"<<([^<>\n]{1,40})>>")
DUNDER_RE = re.compile(r"__[A-Z][A-Z_]{2,}__")
DRAFT_MARKER_RE = re.compile(r"(?<![\w])(?:TODO|TBD|FIXME|XXX)(?![\w])")
LOREM_RE = re.compile(r"lorem ipsum", re.IGNORECASE)
PLACEHOLDER_WORD_RE = re.compile(r"(?<![\w-])placeholder(?![\w-])", re.IGNORECASE)
LITERAL_RE = re.compile(r"(?<![\w.$])(undefined|null|NaN)(?![\w.(])")
# undefined/null/NaN sadece kısa öğe metinlerinde sayılır ("Hoş geldin, undefined");
# uzun hukuki/teknik metinlerde ("null and void") geçmesi normaldir.
MAX_LITERAL_ELEMENT_CHARS = 60
DRAFT_PHRASE_GAP = 4  # "buraya ... yazın": arada en fazla bu kadar kelime


@dataclass
class Placeholder:
    text: str
    kind: str  # contact | template | draft | lorem | literal


def _draft_phrase_re() -> re.Pattern[str] | None:
    starts = keywords.pattern("placeholders.draft_phrase_start")
    ends = keywords.pattern("placeholders.draft_phrase_end")
    if starts is None or ends is None:
        return None
    gap = rf"(?:\s+\S+){{0,{DRAFT_PHRASE_GAP}}}?\s+"
    return re.compile(f"(?:{starts.pattern}){gap}(?:{ends.pattern})")


def _classify_content(content: str) -> str | None:
    """Parantez içi yer tutucunun türü; yer tutucu kelimesi yoksa None ("[1]", "[Güncellendi]")."""
    folded = fold(content)
    if "@" in content or keywords.has("placeholders.example_domains", folded):
        return "contact"
    if keywords.has("placeholders.contact_words", folded):
        return "contact"
    if keywords.has("placeholders.content_words", folded):
        return "template"
    return None


def _example_domain_tokens(text: str) -> Iterator[str]:
    for token in text.split():
        if keywords.has("placeholders.example_domains", fold(token)):
            yield token.strip(".,;:()<>\"'")


def find_placeholders(text: str, client_templates: bool) -> list[Placeholder]:
    """Bir öğenin KENDİ metnindeki yer tutucular."""
    found: list[Placeholder] = []
    for rx in (BRACKET_RE, ANGLE_RE):
        for m in rx.finditer(text):
            kind = _classify_content(m.group(1))
            if kind:
                found.append(Placeholder(m.group(0), kind))
    if not client_templates:
        found += [Placeholder(m.group(0), "template") for m in MUSTACHE_RE.finditer(text)]
    found += [Placeholder(m.group(0), "template") for m in DUNDER_RE.finditer(text)]
    covered = " ".join(p.text for p in found)
    found += [Placeholder(t, "contact") for t in _example_domain_tokens(text) if t not in covered]
    found += [Placeholder(m.group(0), "draft") for m in DRAFT_MARKER_RE.finditer(text)]
    found += [Placeholder(m.group(0), "draft") for m in PLACEHOLDER_WORD_RE.finditer(text)]
    phrase = _draft_phrase_re()
    if phrase is not None:
        found += [Placeholder(m.group(0), "draft") for m in phrase.finditer(fold(text))]
    found += [Placeholder(m.group(0), "lorem") for m in LOREM_RE.finditer(text)]
    if len(text) <= MAX_LITERAL_ELEMENT_CHARS:
        found += [Placeholder(m.group(1), "literal") for m in LITERAL_RE.finditer(text)]
    return found


def _clean_tree(html: str) -> LexborHTMLParser:
    tree = LexborHTMLParser(html)
    for node in tree.css(", ".join(CODE_TAGS)):
        node.decompose()
    return tree


def _own_texts(tree: LexborHTMLParser) -> Iterator[str]:
    root = tree.body
    if root is None:
        return
    for node in root.traverse(include_text=False):
        text = squash(node.text(deep=False) or "")
        if text:
            yield text


def _context(full_text: str, needle: str) -> str:
    index = full_text.find(needle)
    if index < 0:
        return ""
    start = max(0, index - CONTEXT_CHARS)
    return full_text[start : index + len(needle) + CONTEXT_CHARS]


def _href_placeholders(tree: LexborHTMLParser, client_templates: bool) -> list[Placeholder]:
    found = []
    for node in tree.css("a[href]"):
        href = attr(node, "href")
        if keywords.has("placeholders.example_domains", fold(href)):
            found.append(Placeholder(href[:MAX_MATCH_CHARS], "contact"))
        elif not client_templates and MUSTACHE_RE.search(href):
            found.append(Placeholder(href[:MAX_MATCH_CHARS], "template"))
    return found


def page_placeholders(page: PageSnapshot) -> list[tuple[Placeholder, str]]:
    """(yer tutucu, bağlam) — kod blokları ve input placeholder'ları hariç."""
    client_templates = bool(CLIENT_TEMPLATE_RE.search(page.html))
    tree = _clean_tree(page.html)
    body = tree.body
    full_text = squash(body.text(deep=True, separator=" ")) if body is not None else ""
    out: list[tuple[Placeholder, str]] = []
    seen: set[str] = set()
    for text in _own_texts(tree):
        for ph in find_placeholders(text, client_templates):
            if ph.text not in seen:
                seen.add(ph.text)
                out.append((ph, _context(full_text, ph.text)))
    for ph in _href_placeholders(tree, client_templates):
        if ph.text not in seen:
            seen.add(ph.text)
            out.append((ph, ""))
    return out


def _severity(kind: str, legal: bool, special_data: bool) -> Severity:
    if not legal:
        return Severity.MEDIUM
    if kind == "contact":
        return Severity.CRITICAL if special_data else Severity.HIGH
    return Severity.HIGH


@register
class TemplatePlaceholders(WebCheck):
    id = "web.content.template_placeholders"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM
    considers_special_data = True

    async def check(self, ctx: WebContext) -> list[Finding]:
        special = content_signals(ctx).collects_special_category_data
        pages = [*ctx.pages, *await extra_info_pages(ctx, "legal")]
        groups: dict[bool, list[dict[str, Any]]] = {True: [], False: []}
        worst: dict[bool, Severity] = {}
        seen_urls: set[str] = set()
        for page in pages:
            if page.url in seen_urls:
                continue
            seen_urls.add(page.url)
            tree = page_tree(ctx, page) if page in ctx.pages else LexborHTMLParser(page.html)
            legal = is_legal_page(page.url, tree)
            for ph, context in page_placeholders(page):
                groups[legal].append(
                    {
                        "page": page.url,
                        "text": ph.text[:MAX_MATCH_CHARS],
                        "context": context,
                        "legal_page": legal,
                        "kind": ph.kind,
                    }
                )
                level = _severity(ph.kind, legal, special)
                current = worst.get(legal)
                worst[legal] = level if current is None or level.rank < current.rank else current
        findings = []
        for legal, matches in groups.items():
            if not matches:
                continue
            findings.append(
                Finding(
                    check_id=self.id,
                    severity=worst[legal],
                    # Yasal metindeki eksik kullanıcının haklarını etkiler: güvenlik/uyum
                    category=Category.SECURITY if legal else Category.PROD_READINESS,
                    evidence={"matches": matches[:MAX_MATCHES], "count": len(matches)},
                )
            )
        return findings

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        legal = any(m.get("legal_page") for m in evidence.get("matches") or [])
        key = "legal" if legal else "other"
        return {"headline": fragment(f"headline_{key}"), "situation": fragment(f"situation_{key}")}


# --- KVKK yurt dışı aktarım

BLOCK_TAGS = "h1, h2, h3, h4, h5, h6, p, li, td, dd, blockquote"
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
TURKISH_CHARS_RE = re.compile(r"[çğışöüÇĞİŞÖÜ]")
MIN_TURKISH_CHARS = 20
CONSENT_WINDOW = 160
MAX_DESTINATIONS = 8


def is_turkish(page: PageSnapshot, tree: LexborHTMLParser) -> bool:
    html = tree.css_first("html")
    lang = attr(html, "lang").lower() if html is not None else ""
    if lang:
        return lang.startswith("tr")
    return len(TURKISH_CHARS_RE.findall(page.html)) >= MIN_TURKISH_CHARS


def sections(tree: LexborHTMLParser) -> list[str]:
    """Başlıklara göre bölümler (sade metin). Başlık yoksa her paragraf bir bölümdür."""
    blocks: list[tuple[bool, str]] = []
    for node in tree.css(BLOCK_TAGS):
        if _inside_other_block(node):
            continue
        text = fold(squash(node.text(deep=True, separator=" ")))
        if text:
            blocks.append((node.tag in HEADING_TAGS, text))
    has_headings = any(is_heading for is_heading, _ in blocks)
    out: list[str] = []
    for is_heading, text in blocks:
        if not has_headings or is_heading or not out:
            out.append(text)
        else:
            out[-1] += " " + text
    return out


def _inside_other_block(node: LexborNode) -> bool:
    """`li > p` gibi iç içe bloklar iki kez sayılmasın."""
    current = node.parent
    while current is not None and current.tag not in ("body", "html"):
        if current.tag in ("p", "li", "td", "dd", "blockquote"):
            return True
        current = current.parent
    return False


def consent_conditioned_on_service(text: str) -> bool:
    """ "rıza" kelimesinin yakınında hizmetin verilemeyeceği söyleniyor mu?"""
    for match in re.finditer(r"(?<![a-z])(?:riza|consent)", text):
        window = text[max(0, match.start() - CONSENT_WINDOW) : match.end() + CONSENT_WINDOW]
        if keywords.has("transfer.service_denied", window):
            return True
    return False


def transfer_analysis(tree: LexborHTMLParser) -> dict[str, Any] | None:
    parts = sections(tree)
    document = " ".join(parts)
    transfer_parts = [s for s in parts if keywords.has("transfer.section", s)]
    if not transfer_parts:
        return None
    consent_only = any(
        keywords.has("transfer.consent", s) for s in transfer_parts
    ) and not keywords.has("transfer.safeguards", document)
    conditioned = consent_conditioned_on_service(document)
    countries = keywords.word_map("countries")
    transfer_text = " ".join(transfer_parts)
    destinations = []
    for key, name in countries.items():
        if keywords.has_word(key, transfer_text) and name not in destinations:
            destinations.append(name)
    return {
        "transfer_basis_consent_only": consent_only,
        "consent_conditioned_on_service": conditioned,
        "mentioned_destinations": destinations[:MAX_DESTINATIONS],
        "amendment_referenced": any(
            r in document for r in keywords.words("transfer.amendment_refs")
        ),
    }


@register
class KvkkTransferBasis(WebCheck):
    id = "web.privacy.kvkk_transfer_basis"
    category = Category.SECURITY
    default_severity = Severity.LOW
    considers_special_data = True

    async def check(self, ctx: WebContext) -> list[Finding]:
        candidates = [(p, page_tree(ctx, p)) for p in ctx.pages]
        candidates += [(p, LexborHTMLParser(p.html)) for p in await extra_info_pages(ctx, "legal")]
        hits: list[tuple[str, dict[str, Any]]] = []
        for page, tree in candidates:
            if not is_legal_page(page.url, tree) or not is_turkish(page, tree):
                continue
            result = transfer_analysis(tree)
            if result and (
                result["transfer_basis_consent_only"] or result["consent_conditioned_on_service"]
            ):
                hits.append((page.url, result))
        if not hits:
            return []
        merged: dict[str, Any] = {
            "page": hits[0][0],
            "pages": [url for url, _ in hits],
            "transfer_basis_consent_only": any(r["transfer_basis_consent_only"] for _, r in hits),
            "consent_conditioned_on_service": any(
                r["consent_conditioned_on_service"] for _, r in hits
            ),
            "mentioned_destinations": list(
                dict.fromkeys(d for _, r in hits for d in r["mentioned_destinations"])
            )[:MAX_DESTINATIONS],
            "amendment_referenced": any(r["amendment_referenced"] for _, r in hits),
        }
        special = content_signals(ctx).collects_special_category_data
        severity = (
            Severity.MEDIUM if merged["transfer_basis_consent_only"] and special else Severity.LOW
        )
        return [self.finding(merged, severity=severity)]
