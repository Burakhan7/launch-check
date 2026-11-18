"""Bulgu ve hata metinlerinin dil bazlı yüklenmesi. Metinler DB'de değil, burada (tr/en YAML)."""

from collections.abc import Callable
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


def _read_yaml(package: str, filename: str) -> dict[str, Any]:
    data = yaml.safe_load(resources.files(package).joinpath(filename).read_text("utf-8"))
    return data or {}


@lru_cache
def load_catalog(lang: str) -> dict[str, Any]:
    """`{lang}.yaml` (hatalar) + `checks/*.{lang}.yaml` (bulgu metinleri, gruba göre)."""
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"desteklenmeyen dil: {lang}")
    data = _read_yaml("launchcheck.i18n", f"{lang}.yaml")
    checks: dict[str, Any] = dict(data.get("checks") or {})
    for entry in sorted(resources.files("launchcheck.i18n.checks").iterdir(), key=lambda e: e.name):
        if entry.name.endswith(f".{lang}.yaml"):
            group = _read_yaml("launchcheck.i18n.checks", entry.name)
            duplicates = set(group) & set(checks)
            if duplicates:
                raise ValueError(f"tekrarlanan check metni: {sorted(duplicates)}")
            checks.update(group)
    data["checks"] = checks
    data.setdefault("errors", {})
    return data


def _lookup_check(check_id: str, lang: str) -> dict[str, Any] | None:
    for candidate in (lang, DEFAULT_LANG, *SUPPORTED_LANGS):
        entry = (load_catalog(candidate)["checks"] or {}).get(check_id)
        if entry:
            return entry
    return None


class _KeepMissing(dict[str, str]):
    """Bilinmeyen yer tutucuyu olduğu gibi bırakır (KeyError yerine)."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render(template: str, params: dict[str, str]) -> str:
    if not params or "{" not in template:
        return template
    try:
        return template.format_map(_KeepMissing(params))
    except (ValueError, IndexError):  # metinde süslü parantez bozuksa çökme
        return template


def fragment_fn(check_id: str, lang: str) -> Callable[..., str]:
    """Check'in i18n `fragments` alanından dile özgü metin parçası üretir."""
    entry = _lookup_check(check_id, normalize_lang(lang)) or {}
    fragments: dict[str, Any] = entry.get("fragments") or {}

    def fragment(name: str, **values: object) -> str:
        template = str(fragments.get(name, name))
        return _render(template, {k: str(v) for k, v in values.items()})

    return fragment


def finding_text(check_id: str, lang: str, params: dict[str, str] | None = None) -> FindingText:
    """`params` metinlerdeki `{yer_tutucu}`ları doldurur (check.text_params üretir).
    Eksik metinler testte yakalanır; çalışma anında yine de çökmemek için yedek döner."""
    entry = _lookup_check(check_id, normalize_lang(lang))
    if entry is None:
        return FindingText(
            title=check_id, description="", why_it_matters="", fix_steps=[], fix_prompt=""
        )
    values = params or {}
    # Dinamik adımlar: tamamı tek bir yer tutucu olan adım ("{npm_token_step}") parametre boş
    # gelirse listeden düşer; böylece adım sadece ilgili durumda görünür.
    steps = [_render(str(s), values) for s in entry.get("fix_steps") or []]
    return FindingText(
        title=_render(str(entry.get("title", check_id)), values),
        description=_render(str(entry.get("description", "")), values),
        why_it_matters=_render(str(entry.get("why_it_matters", "")), values),
        fix_steps=[s for s in steps if s.strip()],
        fix_prompt=_render(str(entry.get("fix_prompt", "")).strip(), values),
    )


def passed_title(check_id: str, lang: str) -> str:
    """ "Geçti" listesi başlığı. Şablonlu başlıklar (ör. {headline}) parametresiz anlamsız
    olduğu için bu check'ler i18n'de ayrı bir `passed_title` tanımlar."""
    entry = _lookup_check(check_id, normalize_lang(lang)) or {}
    return str(entry.get("passed_title") or entry.get("title") or check_id)


def error_message(code: str, lang: str) -> str:
    errors = load_catalog(normalize_lang(lang))["errors"]
    return str(errors.get(code) or errors.get(FALLBACK_ERROR_CODE) or code)
