"""web.conversion.value_proposition — değer önerisi değerlendirmesi (LLM, Faz 2).

Bu check kurala dökülemez. Faz 1'de kayıtlıdır ama `phase = 2` olduğu için Faz 1 taramalarında
çalışmaz. Şimdilik hazır olanlar: girdi çıkarımı (`build_input`), prompt (`build_prompt`), çıktı
doğrulaması (`parse_output`) ve bulguya dönüştürme. LLM istemcisi Faz 2'de bağlanacak
(`llm_client`); bağlı değilse check bulgu üretmez.
"""

import json
from typing import Any, ClassVar, Protocol
from urllib.parse import urljoin

from pydantic import ValidationError
from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Category, Finding, ScanContext, Severity, TargetType
from launchcheck.checks.conversion.llm.schema import (
    RUBRIC,
    CtaLink,
    ValuePropositionInput,
    ValuePropositionOutput,
)
from launchcheck.checks.registry import register
from launchcheck.checks.shared.text import squash
from launchcheck.checks.web._common import WebCheck, meta_content, page_tree
from launchcheck.checks.web._html import ancestor, attr, location_of
from launchcheck.collectors.web_collector import WebContext

MAX_HEADINGS = 15
MAX_LINKS = 20
MAX_HERO_CHARS = 1200
HERO_LEVELS = 2
MAX_FINDINGS = 7
META_FLAGS = (
    "is_ugc",
    "has_login",
    "is_dating_or_matching",
    "collects_special_category_data",
    "has_public_community",
)
GUARDRAILS = """Kurallar:
- SADECE aşağıdaki girdideki metinlere dayan. Dış bilgi, piyasa verisi veya tahmin kullanma.
- Piyasa veya rakip bağlamı gerektiren her yorumu `needs_verification: true` ile "doğrulanmalı"
  olarak işaretle.
- `evidence_text` girdide AYNEN geçen bir metin olmalı; geçmiyorsa o bulguyu yazma.
- Öneri metinlerinde rakip marka adı kullanacaksan sonuna "(marka hukuku açısından kontrol et)"
  notunu ekle.
- Her rubrik maddesine 0-2 puan ver; gerekçe ve somut bir öneri yaz.
- Çıktı yalnızca verilen JSON şemasına uygun JSON olsun."""


class LlmClient(Protocol):
    async def complete_json(self, prompt: str, schema: dict[str, Any]) -> str: ...


def _texts(tree: LexborHTMLParser, selector: str, limit: int) -> list[str]:
    out = []
    for node in tree.css(selector):
        text = squash(node.text(deep=True, separator=" "))
        if text and text not in out:
            out.append(text)
    return out[:limit]


def build_input(ctx: WebContext) -> ValuePropositionInput:
    home = ctx.pages[0]
    tree = page_tree(ctx, home)
    h1 = tree.css_first("h1")
    hero = ancestor(h1, HERO_LEVELS) if h1 is not None else None
    hero_text = squash(hero.text(deep=True, separator=" ")) if hero is not None else ""
    ctas, nav, footer = [], [], []
    for node in tree.css("a[href], button"):
        text = squash(node.text(deep=True, separator=" "))
        if not text:
            continue
        where = location_of(node)
        if where == "nav":
            nav.append(text)
        elif where == "footer":
            footer.append(text)
        elif node.tag == "button" or "btn" in attr(node, "class").lower():
            ctas.append(CtaLink(text=text, target=urljoin(home.url, attr(node, "href"))))
    keywords_raw = meta_content(tree, name="keywords")
    flags = {k: v for k in META_FLAGS if isinstance(v := ctx.meta.get(k), bool)}
    return ValuePropositionInput(
        url=home.url,
        title=_texts(tree, "title", 1)[0] if tree.css_first("title") else "",
        meta_description=meta_content(tree, name="description"),
        meta_keywords=[k.strip() for k in keywords_raw.split(",") if k.strip()],
        headings=_texts(tree, "h1, h2, h3", MAX_HEADINGS),
        hero_text=hero_text[:MAX_HERO_CHARS],
        ctas=ctas[:MAX_LINKS],
        nav_links=list(dict.fromkeys(nav))[:MAX_LINKS],
        footer_links=list(dict.fromkeys(footer))[:MAX_LINKS],
        flags=flags,
    )


def build_prompt(data: ValuePropositionInput, lang: str) -> str:
    rubric = "\n".join(
        f"{i + 1}. {item.value}: {text}" for i, (item, text) in enumerate(RUBRIC.items())
    )
    language = "Türkçe" if lang == "tr" else "English"
    return (
        "Bir web sitesinin ana sayfasındaki değer önerisini değerlendir. "
        f"Yanıt dili: {language}.\n\n"
        f"{GUARDRAILS}\n\nRubrik:\n{rubric}\n\n"
        f"Girdi (JSON):\n{data.model_dump_json(indent=2)}\n"
    )


def parse_output(raw: str, data: ValuePropositionInput) -> ValuePropositionOutput | None:
    """Şemaya uymayan çıktı reddedilir; girdide geçmeyen `evidence_text`'li bulgular atılır."""
    try:
        output = ValuePropositionOutput.model_validate_json(raw)
    except ValidationError:
        return None
    source = data.model_dump_json()
    output.findings = [f for f in output.findings if f.evidence_text and f.evidence_text in source]
    return output


SEVERITY_MAP = {
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
    "info": Severity.INFO,
}


@register
class ValueProposition(WebCheck):
    id = "web.conversion.value_proposition"
    target = TargetType.WEB
    phase = 2
    module = "llm"
    category = Category.CONVERSION
    default_severity = Severity.LOW
    llm_client: ClassVar[LlmClient | None] = None  # Faz 2'de bağlanacak

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if self.llm_client is None:
            return []
        return await super().run(ctx)

    async def check(self, ctx: WebContext) -> list[Finding]:
        client = self.llm_client
        if client is None or not ctx.pages:
            return []
        data = build_input(ctx)
        raw = await client.complete_json(
            build_prompt(data, "tr"), ValuePropositionOutput.model_json_schema()
        )
        output = parse_output(raw, data)
        if output is None:
            return []
        evidence: dict[str, Any] = {
            "scores": {k.value: v.score for k, v in output.scores.items()},
            "findings": [json.loads(f.model_dump_json()) for f in output.findings[:MAX_FINDINGS]],
        }
        worst = min(
            (SEVERITY_MAP[f.severity] for f in output.findings), key=lambda s: s.rank, default=None
        )
        if worst is None:
            return []
        return [self.finding(evidence, severity=worst)]
