"""Kullanıcı güvenliği: yaş sınırı gerektiren platformlarda yaş onayı, duygusal destek
platformunda kriz kaynakları.

Pasif: taranan sayfalar + footer'daki en fazla birkaç hakkında/şartlar/yardım sayfası okunur.
Hesap açılmaz, sohbete bağlanılmaz.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.text import fold, phrase_regex
from launchcheck.checks.web._common import WebCheck, page_tree
from launchcheck.checks.web._html import (
    ancestor,
    attr,
    content_signals,
    extra_info_pages,
    page_text,
    tokens,
)
from launchcheck.collectors.web_collector import PageSnapshot, WebContext
from launchcheck.collectors.web_signals import visible_text

MAX_PAGES = 5
MAX_SIGNALS = 8
MAX_CHECKED_PAGES = 10

AGE_STATEMENT_RE = re.compile(
    r"(?<![a-z0-9])(?:18 ?yas|18 ?\+|18 years|yasindan buyuk|resit|yas dogrulama"
    r"|age verification|18 or older|must be 18|adults only)"
)
AGE_INPUT_TOKENS = {"age", "yas", "yasi", "yasim", "yasiniz", "birth", "birthday", "dob"}
AGE_INPUT_PREFIXES = ("dogum", "birthdate", "yasindan")
# Kullanım şartı onay kutusu (çerez onayı gibi genel "kabul" kutuları sayılmaz)
TERMS_WORD_PREFIXES = ("sart", "kosul", "kural", "terms", "rules", "guidelines")
NON_AGE_INPUT_TYPES = ("hidden", "submit", "button", "reset", "search", "image", "file")
NO_SIGNUP_RE = phrase_regex(
    (
        "uye olmaya gerek",
        "uyelik gerekmez",
        "uyelik gerektirmez",
        "uyelik gerekmeden",
        "kayit gerekmez",
        "kayit gerektirmez",
        "kayit olmadan",
        "uye olmadan",
        "uyeliksiz",
        "no sign up",
        "no signup",
        "no sign-up",
        "without signing up",
        "no registration",
        "without registration",
        "no account needed",
    ),
    prefix=True,
)
LABEL_ANCESTOR_LEVELS = 2


async def _pages_with_info(ctx: WebContext) -> list[tuple[PageSnapshot, LexborHTMLParser, str]]:
    """Taranan sayfalar + footer'daki ek bilgi sayfaları: (sayfa, ağaç, sade metin)."""
    out = [(p, page_tree(ctx, p), page_text(ctx, p)) for p in ctx.pages]
    for page in await extra_info_pages(ctx):
        out.append((page, LexborHTMLParser(page.html), visible_text(page.html)))
    return out


def _own_label(node: LexborNode, tree: LexborHTMLParser) -> str:
    """Girdinin adı/kimliği + `label[for]` metni."""
    parts = [attr(node, a) for a in ("name", "id", "aria-label", "placeholder")]
    node_id = attr(node, "id")
    if node_id:
        parts += [
            lbl.text(deep=True) for lbl in tree.css("label[for]") if attr(lbl, "for") == node_id
        ]
    return " ".join(parts)


def _mentions_age(text: str) -> bool:
    words = tokens(text)
    return (
        bool(AGE_STATEMENT_RE.search(fold(text)))
        or any(w in AGE_INPUT_TOKENS for w in words)
        or any(w.startswith(AGE_INPUT_PREFIXES) for w in words)
    )


ADULT_AGE = 18
MAX_NEAR_LABEL_CHARS = 120
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
BIRTH_FIELD_PREFIXES = ("dogum", "birth", "dob")
YEAR_FIELD_TOKENS = {"yil", "yili", "yilin", "yiliniz", "year"}


def _checkbox_label(node: LexborNode, tree: LexborHTMLParser) -> str:
    # Onay kutusunun metni çoğu zaman yanındaki öğededir: yakın kapsayıcıya da bakılır.
    own = _own_label(node, tree)
    return f"{own} {ancestor(node, LABEL_ANCESTOR_LEVELS).text(deep=True, separator=' ')}"


def _is_birth_field(label: str) -> bool:
    words = tokens(label)
    return any(w.startswith(BIRTH_FIELD_PREFIXES) for w in words) or (
        any(w in YEAR_FIELD_TOKENS for w in words) and any(w in AGE_INPUT_TOKENS for w in words)
    )


def _field_label(node: LexborNode, tree: LexborHTMLParser) -> str:
    """Kendi etiketi + kısa yakın kapsayıcı metni (for'suz kardeş <label> deseni için)."""
    near = ancestor(node, 1).text(deep=True, separator=" ")
    return f"{_own_label(node, tree)} {near[:MAX_NEAR_LABEL_CHARS]}"


def birth_field_allows_minors(node: LexborNode, tree: LexborHTMLParser, year: int) -> bool | None:
    """Doğum yılı/tarihi alanı 18 yaş altını seçtiriyor mu? Doğum alanı değilse veya
    anlaşılamıyorsa None."""
    if not _is_birth_field(_field_label(node, tree)):
        return None
    cutoff = year - ADULT_AGE
    if node.tag == "select":
        years = [int(m.group(1)) for o in node.css("option") if (m := YEAR_RE.search(o.text()))]
        return any(y > cutoff for y in years) if years else None
    input_type = attr(node, "type").lower()
    if input_type in ("date", "number", "month"):
        match = YEAR_RE.search(attr(node, "max"))
        return match is None or int(match.group(1)) > cutoff  # max yoksa her yıl girilebilir
    return None


@dataclass
class AgeSignals:
    statement: bool = False  # metinde 18 yaş şartı veya yaş onay kutusu
    gate: bool = False  # yaş/şart onay kutusu veya 18 yaş altını dışlayan doğum alanı
    allows_minors: bool = False  # doğum alanında son 18 yılın yılları seçilebiliyor


def age_signals(tree: LexborHTMLParser, text: str, year: int) -> AgeSignals:
    out = AgeSignals(statement=bool(AGE_STATEMENT_RE.search(text)))
    for node in tree.css("input, select"):
        input_type = attr(node, "type").lower()
        if input_type in NON_AGE_INPUT_TYPES:
            continue
        if input_type == "checkbox":
            label = _checkbox_label(node, tree)
            if _mentions_age(label):
                out.statement = out.gate = True
            elif any(w.startswith(TERMS_WORD_PREFIXES) for w in tokens(label)):
                out.gate = True
            continue
        minors = birth_field_allows_minors(node, tree, year)
        if minors is True:
            out.allows_minors = True
        elif minors is False:
            out.gate = True
    return out


@register
class AgeGateMissing(WebCheck):
    """Yaş sınırı gerektiren platformlar: yabancılarla canlı sesli/görüntülü sohbet,
    herkese açık topluluk + canlı sesli/görüntülü odalar veya flört/eşleşme/evlilik.
    Canlı ders/yayın gibi bire çok yayınlar (yabancılarla eşleşme olmadan) ve evlilik
    konulu bloglar (eşleşme etkileşimi olmadan) kapsam dışıdır."""

    id = "web.safety.age_gate_missing"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        signals = content_signals(ctx)
        types = [
            name
            for name, hit in (
                ("live_media", signals.live_with_strangers),
                ("dating_or_matching", signals.is_dating_or_matching),
                # Herkese açık sunucu/topluluk + sesli/görüntülü oda: yabancılarla canlı temas
                (
                    "public_community_live",
                    signals.has_public_community and signals.has_live_media,
                ),
            )
            if hit
        ]
        if not types:
            return []
        pages = await _pages_with_info(ctx)
        year = datetime.now(UTC).year
        found = [age_signals(tree, text, year) for _, tree, text in pages]
        statement = any(a.statement for a in found)
        allows_minors = any(a.allows_minors for a in found)
        gate = any(a.gate for a in found) and not allows_minors
        if statement and gate:
            return []
        no_signup = any(NO_SIGNUP_RE.search(text) for _, _, text in pages)
        if statement:
            severity = Severity.LOW
        elif "live_media" in types and no_signup:
            severity = Severity.CRITICAL
        else:
            severity = Severity.HIGH
        evidence: dict[str, Any] = {
            "platform_types": types,
            "age_statement_found": statement,
            "age_checkbox_found": gate,
            "allows_under_18_birth_year": allows_minors,
            "no_signup_required": no_signup,
            "pages_checked": [page.url for page, _, _ in pages][:MAX_CHECKED_PAGES],
        }
        if "live_media" in types:
            evidence["live_media_pages"] = (signals.live_media_pages or [ctx.final_url])[:MAX_PAGES]
            evidence["signals"] = signals.live_media_signals[:MAX_SIGNALS]
        return [self.finding(evidence, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        # Eski kayıtlar (web.safety.live_chat_without_age_gate) platform türü taşımaz.
        types = evidence.get("platform_types") or ["live_media"]
        if evidence.get("age_statement_found"):
            key = "statement_only"
        elif "live_media" in types and evidence.get("no_signup_required"):
            key = "anonymous"
        else:
            key = "no_age_limit"
        minors = fragment("under_18_years") if evidence.get("allows_under_18_birth_year") else ""
        return {
            "headline": fragment(f"headline_{key}"),
            "platform": " ".join(fragment(f"platform_{t}") for t in types),
            "situation": fragment(f"situation_{key}"),
            "under_18": minors,
        }


# Ekli hâlleriyle eşleşen ifadeler ("uzmana", "yardım hattımız")
CRISIS_PREFIX_RE = phrase_regex(
    (
        "yardim hatti",
        "destek hatti",
        "psikolojik destek",
        "profesyonel destek",
        "uzman",
        "crisis",
        "helpline",
        "hotline",
        "emergency",
    ),
    prefix=True,
)
# Sadece tam kelime: sadeleştirilmiş metinde "açılır" → "acilir", "kalp krizi" → "krizi"
CRISIS_EXACT_RE = phrase_regex(("acil", "kriz"))
# Numaralar ancak yakınında arama/acil bağlamı varsa sayılır ("112 görüntüleme" sayılmaz)
CRISIS_NUMBERS_RE = phrase_regex(("112", "988", "116 123"))
NUMBER_CONTEXT_RE = phrase_regex(
    ("ara", "arayin", "arayabilir", "cagri", "hatt", "yardim", "destek", "call", "dial", "help"),
    prefix=True,
)
NUMBER_CONTEXT_CHARS = 40
SELF_HARM_OPTION_RE = phrase_regex(
    ("zarar riski", "kendine zarar", "kendine ya da baskasina zarar", "self-harm", "self harm"),
    prefix=True,
)
SHORT_ELEMENT_CHARS = 80


def has_crisis_info(text: str) -> bool:
    if CRISIS_PREFIX_RE.search(text) or CRISIS_EXACT_RE.search(text):
        return True
    for match in CRISIS_NUMBERS_RE.finditer(text):
        start = max(0, match.start() - NUMBER_CONTEXT_CHARS)
        if NUMBER_CONTEXT_RE.search(text[start : match.end() + NUMBER_CONTEXT_CHARS]):
            return True
    return False


def has_self_harm_report_option(tree: LexborHTMLParser) -> bool:
    """Bildir/şikayet menüsünde 'kendine zarar riski' benzeri bir seçenek (kısa öğe metni)."""
    for node in tree.css("option, label, button, li, a, [role=menuitem], [role=option]"):
        text = node.text(deep=True, separator=" ").strip()
        if text and len(text) <= SHORT_ELEMENT_CHARS and SELF_HARM_OPTION_RE.search(fold(text)):
            return True
    return False


@register
class CrisisResourcesMissing(WebCheck):
    id = "web.safety.crisis_resources_missing"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        signals = content_signals(ctx)
        if not signals.is_emotional_support:
            return []
        pages = await _pages_with_info(ctx)
        found_on = [page.url for page, _, text in pages if has_crisis_info(text)]
        # Gönderi/sohbet/paylaşım sayfaları; tespit edilemezse taranan tüm sayfalar
        post_pages = [
            p.url
            for p in signals.pages
            if p.article or p.comment_section or p.ugc_form or p.live_phrases
        ] or [p.url for p in ctx.pages]
        missing = [url for url in post_pages if url not in found_on]
        if found_on and not missing:
            return []
        evidence: dict[str, Any] = {
            "found_on": found_on[:MAX_PAGES],
            "missing_on_post_pages": bool(missing),
            "post_pages_missing": missing[:MAX_PAGES],
            "self_harm_report_option": any(has_self_harm_report_option(t) for _, t, _ in pages),
        }
        severity = Severity.LOW if found_on else Severity.MEDIUM
        return [self.finding(evidence, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        key = "partial" if evidence.get("found_on") else "none"
        extra = fragment("report_option") if evidence.get("self_harm_report_option") else ""
        return {
            "headline": fragment(f"headline_{key}"),
            "situation": fragment(f"situation_{key}"),
            "report_option": extra,
        }
