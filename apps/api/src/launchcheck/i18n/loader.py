"""Bulgu ve hata metinlerinin dil bazlı yüklenmesi. Metinler DB'de değil, burada (tr/en YAML)."""

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

SUPPORTED_LANGS: tuple[str, ...] = ("tr", "en")
DEFAULT_LANG = "tr"
REQUIRED_CHECK_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "why_it_matters",
    "fix_steps",
    "fix_prompt",
)
FALLBACK_ERROR_CODE = "internal_error"


@dataclass(frozen=True)
class FindingText:
    title: str
    description: str
    why_it_matters: str
    fix_steps: list[str]
    fix_prompt: str


def normalize_lang(lang: str | None) -> str:
    """`en`, `en-US`, `EN` → `en`. Desteklenmeyen/boş → varsayılan dil."""
    if not lang:
        return DEFAULT_LANG
    code = lang.strip().lower().replace("_", "-").split("-", 1)[0]
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


@lru_cache
def load_catalog(lang: str) -> dict[str, Any]:
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"desteklenmeyen dil: {lang}")
    text = resources.files("launchcheck.i18n").joinpath(f"{lang}.yaml").read_text("utf-8")
    data = yaml.safe_load(text) or {}
    data.setdefault("checks", {})
    data.setdefault("errors", {})
    return data


def _lookup_check(check_id: str, lang: str) -> dict[str, Any] | None:
    for candidate in (lang, DEFAULT_LANG, *SUPPORTED_LANGS):
        entry = (load_catalog(candidate)["checks"] or {}).get(check_id)
        if entry:
            return entry
    return None


def finding_text(check_id: str, lang: str) -> FindingText:
    """Eksik metinler testte yakalanır; çalışma anında yine de çökmemek için yedek döner."""
    entry = _lookup_check(check_id, normalize_lang(lang))
    if entry is None:
        return FindingText(
            title=check_id, description="", why_it_matters="", fix_steps=[], fix_prompt=""
        )
    return FindingText(
        title=str(entry.get("title", check_id)),
        description=str(entry.get("description", "")),
        why_it_matters=str(entry.get("why_it_matters", "")),
        fix_steps=[str(s) for s in entry.get("fix_steps") or []],
        fix_prompt=str(entry.get("fix_prompt", "")).strip(),
    )


def error_message(code: str, lang: str) -> str:
    errors = load_catalog(normalize_lang(lang))["errors"]
    return str(errors.get(code) or errors.get(FALLBACK_ERROR_CODE) or code)
