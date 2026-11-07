"""Sayfalar arası tekrar eden başlık ve açıklamalar."""

from collections import defaultdict
from typing import Any
from urllib.parse import urljoin, urlsplit

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck, is_noindex, meta_content
from launchcheck.checks.web._html import attr, iter_pages
from launchcheck.checks.web._shell import covered_urls
from launchcheck.checks.web.render import covered_note
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

MAX_PAGES_PER_GROUP = 10


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _url_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path.rstrip('/') or '/'}"


def _groups(values: dict[str, str]) -> list[dict[str, Any]]:
    """{url: metin} → aynı (normalize) metni kullanan 2+ sayfa grupları."""
    by_text: dict[str, list[str]] = defaultdict(list)
    original: dict[str, str] = {}
    for url, text in values.items():
        key = _norm(text)
        if key:
            by_text[key].append(url)
            original.setdefault(key, " ".join(text.split()))
    return [
        {"text": original[key], "pages": urls[:MAX_PAGES_PER_GROUP], "count": len(urls)}
        for key, urls in by_text.items()
        if len(urls) >= 2
    ]


@register
class DuplicateTitleDescription(WebCheck):
    id = "web.seo.duplicate_title_description"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        settings = get_settings()
        titles: dict[str, str] = {}
        descriptions: dict[str, str] = {}
        seen: set[str] = set()
        covered = covered_urls(ctx)  # ana sayfa kopyası dönen sayfalar ayrı raporlanır
        for page, tree in iter_pages(ctx):
            if is_noindex(page, tree) or page.url in covered:
                continue  # indekslenmeyen sayfanın başlığı aramada görünmez
            canonical = tree.css_first('link[rel="canonical"]')
            href = attr(canonical, "href") if canonical is not None else ""
            key = _url_key(urljoin(page.url, href) if href else page.url)
            if key in seen:
                continue  # aynı canonical'a sahip sayfalar tek sayfadır
            seen.add(key)
            title_node = tree.css_first("title")
            titles[page.url] = title_node.text(deep=True) if title_node is not None else ""
            descriptions[page.url] = meta_content(tree, name="description")

        if len(seen) < settings.web_duplicate_min_pages:
            return []
        dup_titles = _groups(titles)
        dup_descriptions = _groups(descriptions)
        equals_title = [
            url for url, d in descriptions.items() if d and _norm(d) == _norm(titles.get(url, ""))
        ]
        short = [
            url
            for url, d in descriptions.items()
            if d and len(d.strip()) < settings.web_short_description_chars
        ]
        if not (dup_titles or dup_descriptions or equals_title or short):
            return []
        largest = max((g["count"] for g in dup_titles), default=0)
        evidence = {
            "duplicate_titles": [{"title": g["text"], "pages": g["pages"]} for g in dup_titles],
            "duplicate_descriptions": [
                {"description": g["text"], "pages": g["pages"]} for g in dup_descriptions
            ],
            "description_equals_title": equals_title,
            "short_descriptions": short,
            "pages_scanned": len(seen),
            "majority_same_title": largest > len(seen) / 2,
            **covered_note(covered),
        }
        severity = Severity.MEDIUM if dup_titles else Severity.LOW
        return [self.finding(evidence, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        titles = evidence.get("duplicate_titles") or []
        if evidence.get("majority_same_title"):
            headline = fragment("headline_majority")
        elif titles:
            headline = fragment("headline_titles", count=max(len(g["pages"]) for g in titles))
        else:
            headline = fragment("headline_descriptions")
        parts = [fragment("detail_title", title=g["title"], count=len(g["pages"])) for g in titles]
        if evidence.get("duplicate_descriptions"):
            parts.append(fragment("detail_descriptions"))
        if evidence.get("description_equals_title"):
            parts.append(
                fragment("detail_equals_title", count=len(evidence["description_equals_title"]))
            )
        if evidence.get("short_descriptions"):
            parts.append(fragment("detail_short", count=len(evidence["short_descriptions"])))
        return {"headline": headline, "details": "; ".join(parts)}
