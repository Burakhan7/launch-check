"""Her kayıtlı check için tr ve en metinleri eksiksiz olmalı (AGENTS.md §9)."""

import pytest

from launchcheck.checks.registry import load_builtin_checks
from launchcheck.i18n import (
    REQUIRED_CHECK_FIELDS,
    SUPPORTED_LANGS,
    error_message,
    finding_text,
    load_catalog,
    normalize_lang,
)

CHECK_IDS = [c.id for c in load_builtin_checks().all()]


def _check_problems(check_id: str, lang: str) -> list[str]:
    entry = load_catalog(lang)["checks"].get(check_id)
    if not entry:
        return [f"{lang}: {check_id} eksik"]
    problems = []
    for field in REQUIRED_CHECK_FIELDS:
        value = entry.get(field)
        if field == "fix_steps":
            ok = isinstance(value, list) and value and all(isinstance(s, str) and s for s in value)
        else:
            ok = isinstance(value, str) and value.strip()
        if not ok:
            problems.append(f"{lang}: {check_id}.{field} eksik/boş")
    return problems


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_every_check_has_complete_texts(lang: str) -> None:
    problems = [p for cid in CHECK_IDS for p in _check_problems(cid, lang)]
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_no_orphan_check_texts(lang: str) -> None:
    orphans = set(load_catalog(lang)["checks"]) - set(CHECK_IDS)
    assert not orphans, f"kayıtlı olmayan check metni: {sorted(orphans)}"


def test_error_codes_same_in_all_langs() -> None:
    keys = {lang: set(load_catalog(lang)["errors"]) for lang in SUPPORTED_LANGS}
    assert keys["tr"] == keys["en"]


def test_normalize_lang() -> None:
    assert normalize_lang("en-US,en;q=0.9") == "en"
    assert normalize_lang("EN") == "en"
    assert normalize_lang("de") == "tr"
    assert normalize_lang(None) == "tr"


def test_error_message_fallback() -> None:
    assert error_message("not_found", "en").startswith("This report")
    assert error_message("bilinmeyen_kod", "tr") == error_message("internal_error", "tr")


def test_finding_text_fallback_for_unknown_check() -> None:
    text = finding_text("web.unknown.check", "tr")
    assert text.title == "web.unknown.check"
    assert text.fix_steps == []


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_templated_titles_have_passed_title(lang: str) -> None:
    """Başlığı şablonlu ({...}) check'ler "geçti" listesi için sabit passed_title tanımlamalı."""
    problems = []
    for cid in CHECK_IDS:
        entry = load_catalog(lang)["checks"][cid]
        if "{" in str(entry.get("title", "")) and not entry.get("passed_title"):
            problems.append(cid)
    assert not problems, problems


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_passed_titles_have_no_placeholders(lang: str) -> None:
    from launchcheck.i18n import passed_title

    for cid in CHECK_IDS:
        assert "{" not in passed_title(cid, lang), cid
