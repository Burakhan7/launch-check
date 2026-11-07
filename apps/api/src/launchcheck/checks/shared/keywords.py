"""Dil bazlı anahtar kelime listeleri (`launchcheck/keywords/<dil>.yaml`).

Listeler noktalı yolla okunur (`"dating.strong"`) ve `LC_KEYWORD_LANGUAGES` dillerinden
birleştirilir. Sonu `*` ile biten kelime ön ektir; diğerleri tam kelime eşleşir. Eşleşme her
zaman `fold()` uygulanmış (Türkçe karakterleri sadeleştirilmiş, küçük harfli) metinde yapılır.
"""

import re
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from launchcheck.checks.shared.text import fold
from launchcheck.config import get_settings


@lru_cache
def _load(lang: str) -> dict[str, Any]:
    text = resources.files("launchcheck.keywords").joinpath(f"{lang}.yaml").read_text("utf-8")
    data: dict[str, Any] = yaml.safe_load(text) or {}
    return data


def _walk(data: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _langs() -> tuple[str, ...]:
    return tuple(get_settings().keyword_languages)


def words(path: str) -> list[str]:
    """Tüm dillerden birleşik, tekilleştirilmiş liste."""
    out: list[str] = []
    for lang in _langs():
        for word in _walk(_load(lang), path) or []:
            if str(word) not in out:
                out.append(str(word))
    return out


def word_map(path: str) -> dict[str, str]:
    """Anahtar → değer sözlüğü (ör. ülke adı → gösterim adı), tüm dillerden."""
    out: dict[str, str] = {}
    for lang in _langs():
        out.update({str(k): str(v) for k, v in (_walk(_load(lang), path) or {}).items()})
    return out


def sections(path: str) -> list[str]:
    """Bir sözlüğün alt anahtarları (ör. özel nitelikli veri kategorileri)."""
    names: list[str] = []
    for lang in _langs():
        for name in _walk(_load(lang), path) or {}:
            if name not in names:
                names.append(name)
    return names


def compile_words(items: list[str]) -> re.Pattern[str] | None:
    """Kelime listesinden sadeleştirilmiş metinde çalışan regex. Boş listede None."""
    parts = []
    for item in sorted({fold(w) for w in items}, key=len, reverse=True):
        if item.endswith("*"):
            parts.append(re.escape(item[:-1]))
        else:
            parts.append(re.escape(item) + r"(?![a-z0-9])")
    if not parts:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + ")")


@lru_cache(maxsize=256)
def pattern(path: str) -> re.Pattern[str] | None:
    return compile_words(words(path))


@lru_cache(maxsize=2048)
def _item_regex(item: str) -> re.Pattern[str] | None:
    return compile_words([item])


def found(path: str, folded_text: str) -> set[str]:
    """Metinde geçen FARKLI liste öğeleri (çeşitlilik saymak için; "mezhep*" bir öğedir)."""
    rx = pattern(path)
    if rx is None or not rx.search(folded_text):
        return set()
    hits = set()
    for item in words(path):
        item_rx = _item_regex(item)
        if item_rx is not None and item_rx.search(folded_text):
            hits.add(fold(item).rstrip("*"))
    return hits


def has(path: str, folded_text: str) -> bool:
    rx = pattern(path)
    return bool(rx and rx.search(folded_text))


def has_word(item: str, folded_text: str) -> bool:
    """Tek bir öğe (aynı `*` kuralıyla) metinde geçiyor mu?"""
    rx = _item_regex(item)
    return bool(rx and rx.search(folded_text))
