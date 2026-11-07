"""Özel nitelikli kişisel veri (KVKK md.6 / GDPR md.9) toplayan formlarda açık rıza.

Pasif: form gönderilmez. Kanıta alan değerleri değil sadece kategori adları yazılır.
Onay sayılan: form içinde veya formun 3 üst kapsayıcısında, metni açık rıza bildiren bir onay
kutusu; ya da çok adımlı akışlarda ana sayfa CTA'sının açtığı ilk adımda böyle bir kutu.
Footer'daki düz metin linkleri onay sayılmaz.
"""

import re
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold, squash
from launchcheck.checks.web._common import WebCheck, page_tree
from launchcheck.checks.web._html import ancestor, attr, content_signals, location_of
from launchcheck.collectors.web_assets import origin_of
from launchcheck.collectors.web_collector import HTTP_OK, WebContext
from launchcheck.collectors.web_signals import PageSignals

MAX_FORMS = 10
MAX_CTA_PAGES = 3
FORM_CONTAINER_LEVELS = 3
CHECKBOX_LABEL_LEVELS = 2
MAX_CTA_TEXT_CHARS = 40
# Bu kategorilerde onay yoksa high; diğer özel kategorilerde medium
HIGH_RISK_CATEGORIES = {"religion", "politics", "health", "sexual", "ethnicity", "biometric"}
STEP_INDICATOR_RE = re.compile(r"(?<![a-z0-9])(?:adim|step)\s*\d+|\b\d+\s*/\s*\d+\b")


def _container(node: LexborNode) -> LexborNode:
    current = node.parent
    while current is not None:
        if current.tag == "form":
            return current
        current = current.parent
    return ancestor(node, FORM_CONTAINER_LEVELS)


def consent_checkboxes(scope: LexborNode | LexborHTMLParser) -> list[str]:
    """Açık rıza bildiren onay kutularının (sade) metinleri. Footer'dakiler sayılmaz."""
    texts = []
    for box in scope.css('input[type="checkbox"]'):
        if location_of(box) == "footer":
            continue
        text = fold(squash(ancestor(box, CHECKBOX_LABEL_LEVELS).text(deep=True, separator=" ")))
        if keywords.has("explicit_consent", text):
            texts.append(text)
    return texts


def _is_multi_step(container: LexborNode) -> bool:
    text = fold(squash(container.text(deep=True, separator=" ")))
    buttons = " ".join(
        fold(squash(b.text(deep=True, separator=" ")))
        for b in container.css("button, input[type=submit], a[class*=btn]")
    )
    return bool(STEP_INDICATOR_RE.search(text)) or keywords.has("multi_step", buttons)


def _cta_targets(ctx: WebContext) -> list[str]:
    """Ana sayfadaki "Teste başla", "Get started" gibi CTA'ların hedefleri (aynı origin)."""
    home = ctx.pages[0]
    targets: list[str] = []
    for node in page_tree(ctx, home).css("a[href]"):
        text = fold(squash(node.text(deep=True, separator=" ")))
        if not text or len(text) > MAX_CTA_TEXT_CHARS or not keywords.has("cta", text):
            continue
        url = urljoin(home.url, attr(node, "href")).split("#", 1)[0]
        if origin_of(url) == ctx.origin and url not in targets:
            targets.append(url)
    return targets[:MAX_CTA_PAGES]


async def _consent_in_cta_flow(ctx: WebContext, special_pages: set[str]) -> bool:
    """CTA'nın açtığı (hassas sorulardan önceki) sayfada açık rıza onay kutusu var mı?"""
    crawled = {p.url: p for p in ctx.pages}
    for url in _cta_targets(ctx):
        if url in special_pages:
            continue  # CTA doğrudan hassas sorulara gidiyor: önceki adım yok
        if url in crawled:
            tree = page_tree(ctx, crawled[url])
        else:
            res = await ctx.fetch_url(url, follow_redirects=True)
            if res is None or res.status != HTTP_OK or "html" not in res.content_type:
                continue
            tree = LexborHTMLParser(res.text)
        if consent_checkboxes(tree):
            return True
    return False


def _page_entry(page: PageSignals, qualified: set[str]) -> dict[str, Any]:
    """Sayfanın hassas soruları ve kapsayıcılarındaki onay durumu. Grup düğümleri sinyal
    hesaplanırken açılan ağaca bağlıdır (selectolax düğümü ağacı canlı tutar)."""
    groups = [g for g in page.groups if set(g.categories) & qualified]
    containers = {c.mem_id: c for c in (_container(g.node) for g in groups)}.values()
    consents = [t for c in containers for t in consent_checkboxes(c)]
    return {
        "page": page.url,
        "categories": sorted({c for g in groups for c in g.categories} & qualified),
        "has_explicit_consent": bool(consents),
        "multi_step_unverified": False,
        "bundled_consent": any(keywords.has("bundled_purposes", t) for t in consents),
        "_multi_step": any(_is_multi_step(c) for c in containers),
    }


def _severity(entry: dict[str, Any]) -> Severity:
    if entry["has_explicit_consent"]:
        return Severity.LOW  # sadece paket (bundled) rıza durumunda raporlanır
    return Severity.HIGH if HIGH_RISK_CATEGORIES & set(entry["categories"]) else Severity.MEDIUM


@register
class SpecialCategoryWithoutConsent(WebCheck):
    id = "web.privacy.special_category_without_consent"
    category = Category.SECURITY
    default_severity = Severity.HIGH
    considers_special_data = True

    async def check(self, ctx: WebContext) -> list[Finding]:
        signals = content_signals(ctx)
        qualified = set(signals.special_categories)
        if not qualified:
            return []
        entries = [
            _page_entry(page, qualified)
            for page in signals.pages
            if any(set(g.categories) & qualified for g in page.groups)
        ]
        special_pages = {e["page"] for e in entries}
        flow_consent: bool | None = None
        forms = []
        for entry in entries:
            multi_step = entry.pop("_multi_step")
            if not entry["has_explicit_consent"] and multi_step:
                # Önceki adım taranmadıysa ana sayfa CTA'sının açtığı sayfaya bakılır.
                if flow_consent is None:
                    flow_consent = await _consent_in_cta_flow(ctx, special_pages)
                entry["has_explicit_consent"] = flow_consent
                entry["multi_step_unverified"] = not flow_consent
            if not entry["has_explicit_consent"] or entry["bundled_consent"]:
                forms.append(entry)
        if not forms:
            return []
        severity = min((_severity(f) for f in forms), key=lambda s: s.rank)
        return [self.finding({"forms": forms[:MAX_FORMS], "count": len(forms)}, severity=severity)]
