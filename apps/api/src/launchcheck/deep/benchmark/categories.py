"""Referans kategorileri ve kural tabanlı kategori tespiti (PHASE2 §7.1).

Tespit puanlıdır: Faz 1 meta bayrakları (eşleşme platformu, duygusal destek, topluluk...), sayfa
yapısı (fiyat + sepete ekle, uygulama mağazası linkleri) ve başlık/h1/h2 kelimeleri birlikte
sayılır. En yüksek puan en az `MIN_SCORE` değilse veya ikinciyle arası `MIN_MARGIN`'dan azsa
kategori belirsizdir (None). Spec bu durumda LLM'e enum seçtirmeyi öneriyor; P8 ertelendiği için
şimdilik referans örnek kullanılmaz, kullanıcı Faz 2'yi başlatırken kategoriyi seçebilir.
"""

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold

MIN_SCORE = 2
MIN_MARGIN = 1
APP_STORE_HOSTS = ("apps.apple.com", "play.google.com", "itunes.apple.com")
ECOMMERCE_PLATFORMS = {"shopify", "woocommerce", "ikas", "ideasoft", "ticimax"}
KEYWORD_CATEGORIES = ("dev_ai_tools", "nonprofit_org", "ecommerce_brand", "saas")


@dataclass(frozen=True)
class Category:
    key: str
    labels: dict[str, str]
    sites: tuple[str, ...]


@lru_cache
def load_categories() -> tuple[Category, ...]:
    text = (
        resources.files("launchcheck.deep.benchmark")
        .joinpath("reference_sites.yaml")
        .read_text("utf-8")
    )
    data = yaml.safe_load(text) or {}
    return tuple(
        Category(
            key=str(key),
            labels={str(k): str(v) for k, v in (entry.get("label") or {}).items()},
            sites=tuple(str(s).lower() for s in entry.get("sites") or []),
        )
        for key, entry in (data.get("categories") or {}).items()
    )


def category_keys() -> list[str]:
    return [c.key for c in load_categories()]


def category_labels(lang: str) -> list[dict[str, str]]:
    return [
        {"key": c.key, "label": c.labels.get(lang) or c.labels.get("tr") or c.key}
        for c in load_categories()
    ]


@dataclass(frozen=True)
class SiteSignals:
    """Kategori tespiti girdisi (render + Faz 1 meta'sından; tarayıcıdan bağımsız)."""

    meta: dict[str, Any]
    texts: list[str]  # title, h1, h2'ler, ilk ekran metinleri
    external_hosts: list[str]
    prices: int = 0
    add_to_cart: bool = False
    has_pricing_page: bool = False


def scores(s: SiteSignals) -> dict[str, int]:
    meta = s.meta
    out = dict.fromkeys(category_keys(), 0)

    def add(key: str, value: int) -> None:
        if key in out:
            out[key] += value

    if meta.get("is_dating_or_matching"):
        add("dating_matching", 3)
    if meta.get("is_emotional_support"):
        add("wellbeing_support", 3)
    if meta.get("has_public_community"):
        add("community_chat", 2)
    if meta.get("has_live_media") and meta.get("is_ugc"):
        add("community_chat", 1)
    if any(h.endswith(APP_STORE_HOSTS) for h in s.external_hosts):
        add("mobile_app_landing", 2)
    if s.prices and s.add_to_cart:
        add("ecommerce_brand", 2)
    if set(meta.get("platform") or []) & ECOMMERCE_PLATFORMS:
        add("ecommerce_brand", 2)
    if meta.get("has_login") and s.has_pricing_page:
        add("saas", 2)
    text = fold(" | ".join(s.texts))
    for key in KEYWORD_CATEGORIES:
        hits = keywords.found(f"benchmark.{key}", text)
        add(key, min(len(hits), 2))
    return out


def detect_category(s: SiteSignals) -> tuple[str | None, dict[str, int]]:
    result = scores(s)
    ranked = sorted(result.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return None, result
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best_score < MIN_SCORE or best_score - runner_up < MIN_MARGIN:
        return None, result
    return best, result
