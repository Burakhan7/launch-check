"""İçerik sinyalleri: kullanıcı içerikli (UGC) ve hassas platform tespiti → ScanContext.meta.

Saf fonksiyonlar; sadece collector'ın zaten topladığı HTML ve JS'e bakar (ek istek yok).
Bayraklar platform rozetlerinde gösterilir ve UGC'ye özgü check'lerin ön koşuludur.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold, phrase_regex, squash, tokens
from launchcheck.collectors.form_fields import (
    FieldGroup,
    field_groups,
    qualified_categories,
)

NON_VISIBLE_TAGS = ("script", "style", "noscript", "template", "svg")
UGC_BUTTON_RE = phrase_regex(
    ("paylas", "yayinla", "gonder", "post", "publish", "share", "yorum", "comment"), prefix=True
)
CONTACT_INPUT_TOKENS = {"email", "eposta", "mail", "tel", "telefon", "phone", "gsm"}
COMMENT_TOKEN_PREFIXES = ("comment", "yorum")
MIN_ARTICLE_PAGES = 2
TEXTAREA_CONTAINER_LEVELS = 3

LIVE_TEXT_RE = re.compile(
    r"(?<![a-z0-9])(?:goruntulu sohbet|sesli sohbet|canli gorusme|video chat|voice chat"
    r"|random chat|rastgele(?:\s+\S+){0,3}?\s+(?:sohbet|gorusme|chat))"
)
# JS içeriğinde / script adresinde canlı medya izleri → rapordaki sinyal adı
LIVE_JS_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("getUserMedia", re.compile(r"getUserMedia")),
    ("RTCPeerConnection", re.compile(r"RTCPeerConnection")),
    ("livekit", re.compile(r"livekit", re.IGNORECASE)),
    ("mediasoup", re.compile(r"mediasoup", re.IGNORECASE)),
    ("janus", re.compile(r"\bjanus\b", re.IGNORECASE)),
    ("jitsi", re.compile(r"jitsi", re.IGNORECASE)),
    # "agora" tek başına yaygın bir kelime (İspanyolca "şimdi"); SDK adlarıyla sınırlı
    ("agora", re.compile(r"agora(?:rtc|-rtc|\.io)", re.IGNORECASE)),
    ("peerjs", re.compile(r"peerjs", re.IGNORECASE)),
    ("twilio-video", re.compile(r"twilio-video", re.IGNORECASE)),
)
# Duygusal destek: aynı kavramın farklı yazımları tek sayılır, en az 2 farklı kavram gerekir.
EMOTIONAL_CONCEPTS: dict[str, tuple[str, ...]] = {
    "dert": ("dert", "derd"),  # "derdini": ünsüz yumuşaması
    "loneliness": ("yalniz", "loneliness"),
    "depression": ("depresyon", "depression"),
    "anxiety": ("kaygi", "anksiyete", "anxiety"),
    "suicide": ("intihar", "suicid"),
    "self_harm": ("kendine zarar", "self-harm", "self harm"),
    "mental_health": ("ruh sagligi", "mental health"),
    "psychological": ("psikolojik",),
    "emotional_support": ("emotional support",),
}
_EMOTIONAL_RES = {k: phrase_regex(v, prefix=True) for k, v in EMOTIONAL_CONCEPTS.items()}
MIN_EMOTIONAL_CONCEPTS = 2
ANONYMOUS_RE = phrase_regex(("anonim", "anonymous", "takma ad", "rumuz"), prefix=True)
MEDIA_UPLOAD_RE = phrase_regex(
    ("fotograf ekle", "video ekle", "upload photo", "upload image", "upload video")
)


def visible_text(html: str) -> str:
    """Sayfanın görünen metni (script/style hariç), sadeleştirilmiş ve boşlukları teke inmiş."""
    tree = LexborHTMLParser(html)
    for node in tree.css(", ".join(NON_VISIBLE_TAGS)):
        node.decompose()
    root = tree.body or tree.root
    text = root.text(deep=True, separator=" ") if root is not None else ""
    return squash(fold(text))


def _attr(node: LexborNode, name: str) -> str:
    value: Any = node.attributes.get(name)
    return str(value) if value else ""


def _ancestor(node: LexborNode, levels: int) -> LexborNode:
    current = node
    for _ in range(levels):
        parent = current.parent
        if parent is None or parent.tag in ("html", "body", "#document", None):
            break
        current = parent
    return current


def _closest_form(node: LexborNode) -> LexborNode | None:
    current = node.parent
    while current is not None:
        if current.tag == "form":
            return current
        current = current.parent
    return None


def _is_contact_input(node: LexborNode) -> bool:
    if _attr(node, "type").lower() in ("email", "tel"):
        return True
    words = tokens(" ".join(_attr(node, a) for a in ("name", "id", "placeholder", "autocomplete")))
    return any(w in CONTACT_INPUT_TOKENS for w in words)


def _button_texts(container: LexborNode) -> list[str]:
    texts = [b.text(deep=True, separator=" ") for b in container.css("button")]
    texts += [_attr(b, "aria-label") for b in container.css("button")]
    texts += [_attr(i, "value") for i in container.css('input[type="submit"]')]
    return [fold(t) for t in texts if t]


def has_ugc_form(tree: LexborHTMLParser) -> bool:
    """Metin alanı + 'paylaş/gönder/yayınla' düğmesi; iletişim formları (e-posta/telefon) hariç."""
    for textarea in tree.css("textarea"):
        container = _closest_form(textarea) or _ancestor(textarea, TEXTAREA_CONTAINER_LEVELS)
        if any(_is_contact_input(n) for n in container.css("input")):
            continue
        if any(UGC_BUTTON_RE.search(t) for t in _button_texts(container)):
            return True
    return False


def has_comment_section(tree: LexborHTMLParser) -> bool:
    for node in tree.css("[id], [class]"):
        words = tokens(f"{_attr(node, 'id')} {_attr(node, 'class')}")
        if any(w.startswith(COMMENT_TOKEN_PREFIXES) for w in words):
            return True
    return False


def meta_value(tree: LexborHTMLParser, key: str) -> str:
    """`<meta property|name=key content=...>` değeri (anahtar küçük harfle verilir)."""
    for node in tree.css("meta"):
        name = (_attr(node, "property") or _attr(node, "name")).lower()
        if name == key:
            return _attr(node, "content").strip()
    return ""


@dataclass
class PageSignals:
    url: str
    text: str  # sadeleştirilmiş görünen metin
    ugc_form: bool = False
    comment_section: bool = False
    article: bool = False  # og:type=article veya article:author var
    article_with_author: bool = False  # ikisi birden
    live_phrases: list[str] = field(default_factory=list)
    live_js: list[str] = field(default_factory=list)  # sayfanın inline script'lerinde
    file_input: bool = False
    groups: list[FieldGroup] = field(default_factory=list)  # form soruları
    login: bool = False  # şifre alanı veya giriş/kayıt linki/düğmesi
    surface: str = ""  # başlık + meta açıklama + h1/h2 + düğme/CTA metinleri (sade)

    @property
    def has_live_media(self) -> bool:
        return bool(self.live_phrases or self.live_js)


@dataclass
class ContentSignals:
    pages: list[PageSignals]
    live_js_markers: list[str] = field(default_factory=list)
    is_ugc: bool = False
    has_live_media: bool = False
    is_emotional_support: bool = False
    has_anonymous_posting: bool = False
    has_media_upload: bool = False
    emotional_concepts: list[str] = field(default_factory=list)
    special_categories: list[str] = field(default_factory=list)
    is_dating_or_matching: bool = False
    has_login: bool = False
    live_with_strangers: bool = False  # canlı medya + yabancılarla/rastgele görüşme bağlamı

    @property
    def collects_special_category_data(self) -> bool:
        return bool(self.special_categories)

    def flags(self) -> dict[str, Any]:
        return {
            "collects_special_category_data": self.collects_special_category_data,
            "special_categories": self.special_categories,
            "is_dating_or_matching": self.is_dating_or_matching,
            "has_login": self.has_login,
            "is_ugc": self.is_ugc,
            "has_live_media": self.has_live_media,
            "is_emotional_support": self.is_emotional_support,
            "has_anonymous_posting": self.has_anonymous_posting,
            "has_media_upload": self.has_media_upload,
        }

    @property
    def live_media_signals(self) -> list[str]:
        per_page = [s for page in self.pages for s in (*page.live_js, *page.live_phrases)]
        return list(dict.fromkeys([*self.live_js_markers, *per_page]))

    @property
    def live_media_pages(self) -> list[str]:
        return [p.url for p in self.pages if p.has_live_media]


SURFACE_SELECTORS = "title, h1, h2, button, a[class*=btn], a[class*=button], [role=button]"
MAX_LOGIN_TEXT_CHARS = 30
MIN_DATING_SIGNALS = 2


def _is_login_link(href: str, text: str) -> bool:
    """Son yol parçası giriş/kayıt yolu (/giris, /login) veya kısa link metni "Giriş yap"."""
    segments = [fold(s) for s in urlsplit(href).path.split("/") if s]
    if segments and segments[-1] in {fold(w) for w in keywords.words("login.paths")}:
        return True
    short = fold(squash(text))
    return len(short) <= MAX_LOGIN_TEXT_CHARS and keywords.has("login.texts", short)


def has_login_ui(tree: LexborHTMLParser) -> bool:
    if tree.css_first('input[type="password"]') is not None:
        return True
    return any(
        _is_login_link(_attr(n, "href"), n.text(deep=True, separator=" "))
        for n in tree.css("a[href]")
    )


def surface_text(tree: LexborHTMLParser) -> str:
    """Ürünü anlatan yüzey: başlık, açıklama, ana başlıklar ve düğmeler (makale gövdesi değil)."""
    parts = [n.text(deep=True, separator=" ") for n in tree.css(SURFACE_SELECTORS)]
    parts.append(meta_value(tree, "description"))
    return fold(squash(" ".join(parts)))


def page_signals(url: str, html: str) -> PageSignals:
    tree = LexborHTMLParser(html)
    text = visible_text(html)
    og_type = meta_value(tree, "og:type").lower()
    author = bool(meta_value(tree, "article:author"))
    return PageSignals(
        url=url,
        text=text,
        ugc_form=has_ugc_form(tree),
        comment_section=has_comment_section(tree),
        article=og_type == "article" or author,
        article_with_author=og_type == "article" and author,
        live_phrases=list(dict.fromkeys(m.group(0) for m in LIVE_TEXT_RE.finditer(text))),
        live_js=live_js_markers(n.text(deep=True) or "" for n in tree.css("script:not([src])")),
        file_input=tree.css_first('input[type="file"]') is not None,
        groups=field_groups(tree),
        login=has_login_ui(tree),
        surface=surface_text(tree),
    )


def detect_dating(pages: list[PageSignals], has_login: bool) -> bool:
    """Konu (metinde en az 2 farklı sinyal, biri eşleşme mekaniği) + etkileşim: form
    sorularında flört/eşleşme ifadesi, ya da giriş sistemi + ürün yüzeyinde (başlık, ana
    başlıklar, düğmeler) eşleşme mekaniği. Evlilik konulu bir blog konuyu taşır, yorum formu
    da olabilir, ama eşleşme etkileşimi taşımaz."""
    text = " ".join(p.text for p in pages)
    strong = keywords.found("dating.strong", text)
    weak = keywords.found("dating.weak", text)
    if not strong or len(strong | weak) < MIN_DATING_SIGNALS:
        return False
    for page in pages:
        group_texts = " ".join(g.text for g in page.groups)
        if has_login and keywords.has("dating.strong", page.surface):
            return True
        if keywords.has("dating.strong", group_texts) or keywords.has("dating.weak", group_texts):
            return True
    return False


def live_js_markers(js_sources: Iterable[str]) -> list[str]:
    found: list[str] = []
    for source in js_sources:
        for name, pattern in LIVE_JS_MARKERS:
            if name not in found and pattern.search(source):
                found.append(name)
    return found


def analyze_content(pages: Iterable[tuple[str, str]], js_sources: Iterable[str]) -> ContentSignals:
    """`pages`: (url, html). `js_sources`: script içerikleri ve script adresleri."""
    page_list = [page_signals(url, html) for url, html in pages]
    all_text = " ".join(p.text for p in page_list)
    markers = live_js_markers(js_sources)
    concepts = [k for k, rx in _EMOTIONAL_RES.items() if rx.search(all_text)]
    ugc_form = any(p.ugc_form for p in page_list)
    articles = sum(p.article_with_author for p in page_list)
    login = any(p.login for p in page_list)
    live = bool(markers) or any(p.has_live_media for p in page_list)
    return ContentSignals(
        pages=page_list,
        live_js_markers=markers,
        is_ugc=ugc_form
        or any(p.comment_section for p in page_list)
        or articles >= MIN_ARTICLE_PAGES,
        has_live_media=live,
        is_emotional_support=len(concepts) >= MIN_EMOTIONAL_CONCEPTS,
        has_anonymous_posting=ugc_form and bool(ANONYMOUS_RE.search(all_text)),
        has_media_upload=any(p.file_input for p in page_list)
        or bool(MEDIA_UPLOAD_RE.search(all_text)),
        emotional_concepts=concepts,
        special_categories=qualified_categories([g for p in page_list for g in p.groups]),
        is_dating_or_matching=detect_dating(page_list, login),
        has_login=login,
        live_with_strangers=live and keywords.has("live_strangers", all_text),
    )
