"""Herkese açık sayfalarda iç yetki/rol adları ve yönetici hesapları.

Kaynaklar: görünür metin (kod blokları hariç) ve HTML'e gömülü JSON (`__NEXT_DATA__`,
`application/json` script'leri, `window.__INITIAL_STATE__ = {...}` gibi atamalar).
Kanıta kullanıcı adları ve alan DEĞERLERİ yazılmaz; sadece sayfa URL'si, rol enum adı ve
alan ADLARI.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.text import squash
from launchcheck.checks.web._common import WebCheck
from launchcheck.checks.web._html import attr, iter_pages, location_of
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

MAX_PAGES = 10
MAX_ITEMS = 10
MAX_JSON_NODES = 50_000
MAX_JSON_DEPTH = 40
CODE_TAGS = ("code", "pre", "kbd", "samp", "script", "style", "textarea", "template")
STATE_ASSIGN_RE = re.compile(
    r"(?:window\.)?(__[A-Z_]*STATE__|__NUXT__|__APOLLO_STATE__|__PRELOADED_STATE__)\s*=\s*"
)
CAMEL_ADMIN_RE = re.compile(r"\b(?:superAdmin|isAdmin|isSuperuser|isStaff)\b")


def _role_names() -> set[str]:
    return {r.upper() for r in get_settings().web_role_enum_names}


def _enum_in_text(text: str, roles: set[str]) -> str | None:
    """Öğenin kendi metni TEK bir büyük harf rol enum'u ise (rozet/etiket): "SUPER_ADMIN".
    "SYSTEM REQUIREMENTS" gibi büyük harfli başlıklar ve "Yönetici" gibi etiketler sayılmaz."""
    token = text.strip().strip("()[]{}:,.")
    if " " in token or token not in roles or token.upper() != token:
        return None
    return token


def visible_role_labels(tree: LexborHTMLParser) -> list[str]:
    clean = LexborHTMLParser(tree.html or "")
    for node in clean.css(", ".join(CODE_TAGS)):
        node.decompose()
    roles = _role_names()
    found: list[str] = []
    body = clean.body
    if body is None:
        return found
    for node in body.traverse(include_text=False):
        text = squash(node.text(deep=False) or "")
        if not text or location_of(node) == "nav":
            continue
        label = _enum_in_text(text, roles)
        if label and label not in found:
            found.append(label)
    return found


def _extract_json_object(text: str, start: int) -> str | None:
    """`start`'tan itibaren dengeli süslü parantezli ilk nesne (string içleri dikkate alınır)."""
    begin = text.find("{", start)
    if begin < 0:
        return None
    depth, in_str, escape = 0, False, False
    for i in range(begin, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[begin : i + 1]
    return None


def embedded_json(tree: LexborHTMLParser) -> Iterator[Any]:
    for node in tree.css("script"):
        script_type = attr(node, "type").lower()
        body = node.text(deep=True) or ""
        if attr(node, "id") == "__NEXT_DATA__" or script_type == "application/json":
            yield from _loads(body)
            continue
        if script_type not in ("", "text/javascript", "module"):
            continue
        for match in STATE_ASSIGN_RE.finditer(body):
            raw = _extract_json_object(body, match.end())
            if raw:
                yield from _loads(raw)


def _loads(raw: str) -> Iterator[Any]:
    try:
        yield json.loads(raw)
    except ValueError:
        return


def _walk_dicts(data: Any) -> Iterator[dict[str, Any]]:
    stack: list[tuple[Any, int]] = [(data, 0)]
    seen = 0
    while stack and seen < MAX_JSON_NODES:
        item, depth = stack.pop()
        seen += 1
        if depth > MAX_JSON_DEPTH:
            continue
        if isinstance(item, dict):
            yield item
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)


def _admin_value(key: str, value: Any, roles: set[str]) -> tuple[bool, str | None]:
    """(yönetici/iç rol mü, rol ENUM adı). `is_admin: true` gibi bayraklarda ad yoktur."""
    if isinstance(value, bool):
        return (value and key.startswith("is")), None
    values = value if isinstance(value, list) else [value]
    for v in values:
        if isinstance(v, str) and v.strip().upper() in roles:
            return True, v.strip().upper()
    return False, None


def json_role_signals(data: Any) -> tuple[list[str], list[str], bool]:
    """(rol etiketleri, alan adları, hassas alanlar birlikte mi) — değerler döndürülmez."""
    settings = get_settings()
    role_keys = {k.lower() for k in settings.web_role_json_keys}
    sensitive_keys = {k.lower() for k in settings.web_sensitive_user_json_keys}
    roles = _role_names()
    labels: list[str] = []
    fields: list[str] = []
    sensitive = False
    for obj in _walk_dicts(data):
        keys = {str(k): str(k).lower() for k in obj}
        role_fields = [k for k, low in keys.items() if low in role_keys]
        if not role_fields:
            continue
        hits = [_admin_value(keys[k], obj[k], roles) for k in role_fields]
        admin = any(is_admin for is_admin, _ in hits)
        user_fields = [k for k, low in keys.items() if low in sensitive_keys]
        if user_fields:
            sensitive = True  # rol alanı + e-posta/telefon/hash/token aynı kullanıcı nesnesinde
        if admin or user_fields:
            labels += [name for _, name in hits if name and name not in labels]
            fields += [f for f in [*role_fields, *user_fields] if f not in fields]
    return labels, fields, sensitive


def _camel_flags_in_scripts(tree: LexborHTMLParser) -> list[str]:
    """JSON olarak ayrıştırılamayan inline veride `isAdmin: true` gibi bayraklar."""
    found: list[str] = []
    for node in tree.css("script:not([src])"):
        for m in CAMEL_ADMIN_RE.finditer(node.text(deep=True) or ""):
            tail = (node.text(deep=True) or "")[m.end() : m.end() + 8]
            if re.match(r"""["']?\s*:\s*(?:true|!0)""", tail) and m.group(0) not in found:
                found.append(m.group(0))
    return found


@register
class InternalRoleExposed(WebCheck):
    id = "web.privacy.internal_role_exposed"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        pages: list[str] = []
        labels: list[str] = []
        fields: list[str] = []
        sensitive = False
        for page, tree in iter_pages(ctx):
            page_labels = visible_role_labels(tree)
            page_fields: list[str] = []
            for data in embedded_json(tree):
                json_labels, json_fields, json_sensitive = json_role_signals(data)
                page_labels += [x for x in json_labels if x not in page_labels]
                page_fields += [f for f in json_fields if f not in page_fields]
                sensitive = sensitive or json_sensitive
            page_fields += [f for f in _camel_flags_in_scripts(tree) if f not in page_fields]
            if page_labels or page_fields:
                pages.append(page.url)
                labels += [x for x in page_labels if x not in labels]
                fields += [f for f in page_fields if f not in fields]
        if not pages:
            return []
        evidence = {
            "pages": pages[:MAX_PAGES],
            "role_labels": labels[:MAX_ITEMS],
            "json_fields": fields[:MAX_ITEMS],
            "sensitive_user_fields_in_json": sensitive,
        }
        return [self.finding(evidence, severity=Severity.HIGH if sensitive else Severity.MEDIUM)]
