"""Kullanıcı güvenliği: canlı sohbette yaş sınırı, duygusal destek platformunda kriz kaynakları.

Pasif: taranan sayfalar + footer'daki en fazla birkaç hakkında/şartlar/yardım sayfası okunur.
Hesap açılmaz, sohbete bağlanılmaz.
"""

import re
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


def has_age_gate(tree: LexborHTMLParser) -> bool:
    """Yaş veya kullanım şartı onay kutusu ya da doğum tarihi/yaş girdisi var mı?"""
    for node in tree.css("input, select"):
        input_type = attr(node, "type").lower()
        if input_type in NON_AGE_INPUT_TYPES:
            continue
        own = _own_label(node, tree)
        if input_type != "checkbox":
            if _mentions_age(own):
                return True
            continue
        # Onay kutusunun metni çoğu zaman yanındaki öğededir: yakın kapsayıcıya da bakılır.
        label = f"{own} {ancestor(node, LABEL_ANCESTOR_LEVELS).text(deep=True, separator=' ')}"
        words = tokens(label)
        if _mentions_age(label) or any(w.startswith(TERMS_WORD_PREFIXES) for w in words):
            return True
    return False


def has_age_statement(tree: LexborHTMLParser, text: str) -> bool:
    if AGE_STATEMENT_RE.search(text):
        return True
    return any(
        _mentions_age(_own_label(n, tree))
        for n in tree.css("input, select")
        if attr(n, "type").lower() not in NON_AGE_INPUT_TYPES
    )


@register
class LiveChatWithoutAgeGate(WebCheck):
    id = "web.safety.live_chat_without_age_gate"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        signals = content_signals(ctx)
        if not signals.has_live_media:
            return []
        pages = await _pages_with_info(ctx)
        statement = any(has_age_statement(tree, text) for _, tree, text in pages)
        gate = any(has_age_gate(tree) for _, tree, _ in pages)
        if statement and gate:
            return []
        no_signup = any(NO_SIGNUP_RE.search(text) for _, _, text in pages)
        if statement:
            severity = Severity.LOW
        elif no_signup:
            severity = Severity.CRITICAL
        else:
            severity = Severity.HIGH
        live_pages = signals.live_media_pages or [ctx.final_url]
        evidence: dict[str, Any] = {
            "live_media_pages": live_pages[:MAX_PAGES],
            "signals": signals.live_media_signals[:MAX_SIGNALS],
            "age_statement_found": statement,
            "age_checkbox_found": gate,
            "no_signup_required": no_signup,
        }
        return [self.finding(evidence, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        if evidence.get("age_statement_found"):
            key = "statement_only"
        elif evidence.get("no_signup_required"):
            key = "anonymous"
        else:
            key = "no_age_limit"
        return {"headline": fragment(f"headline_{key}"), "situation": fragment(f"situation_{key}")}


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
