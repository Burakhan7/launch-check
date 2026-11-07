"""Kişisel veri toplayan formlar: KVKK aydınlatma/onay, spam koruması, 3. parti form servisleri.

Pasif: formlar gönderilmez, gömülü form servislerine istek atılmaz. Alan değerleri, CSRF
token'ları ve form kimlikleri kanıta yazılmaz; sadece alan TÜRLERİ yazılır.
"""

from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.checks.web._common import WebCheck
from launchcheck.checks.web._html import (
    attr,
    fold,
    has_consent_near,
    iter_pages,
    page_has_consent,
    tokens,
)
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

MAX_FORMS = 10
MAX_EMBEDS = 20

# Alan türü → anahtar kelimeler (Türkçe karakterler sadeleştirilmiş). Sıra önceliktir.
FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "id_number": ("tckn", "tcno", "tckimlik", "kimlik", "tc"),
    "birth_date": ("dogum", "birth", "birthday", "dob"),
    "email": ("email", "eposta", "mail"),
    "tel": ("telefon", "phone", "gsm", "tel", "cep", "mobile"),
    "name": (
        "adsoyad",
        "soyad",
        "fullname",
        "firstname",
        "lastname",
        "surname",
        "isim",
        "name",
        "ad",
    ),
    "address": ("adres", "address"),
}
SENSITIVE_FIELDS = {"id_number", "birth_date"}
# "ad" gibi çok kısa kelimeler sadece tam eşleşir (ve Türkçe ek almış hâlleri)
SHORT_KEYWORD_FORMS = {"ad": {"ad", "adi", "adin", "adiniz"}, "tc": {"tc"}, "tel": {"tel"}}
NON_DATA_INPUT_TYPES = {"hidden", "submit", "button", "reset", "checkbox", "radio", "image", "file"}
SEARCH_NAMES = {"q", "s", "search", "query", "arama"}
HONEYPOT_NAMES = {"website", "url", "hp", "honeypot", "_gotcha", "_honey"}
SPAM_SELECTORS = (".g-recaptcha", ".h-captcha", ".cf-turnstile", "[data-sitekey]")
SPAM_SCRIPT_MARKERS = (
    "google.com/recaptcha",
    "recaptcha.net",
    "hcaptcha.com",
    "challenges.cloudflare.com/turnstile",
)
FORM_ID_LIKE_MAX = 20
MIN_JOINED_KEYWORD = 5


def _keyword_hit(keyword: str, words: list[str], joined: str) -> bool:
    if keyword in SHORT_KEYWORD_FORMS:
        return any(w in SHORT_KEYWORD_FORMS[keyword] for w in words)
    # İç içe arama sadece uzun kelimelerde: "name" ↛ "username" (giriş formu), "adsoyad" → ✓
    in_joined = len(keyword) >= MIN_JOINED_KEYWORD and keyword in joined
    return keyword in words or any(w.startswith(keyword) for w in words) or in_joined


def classify_field(node: LexborNode, label_text: str) -> str | None:
    input_type = attr(node, "type").lower()
    if input_type == "email":
        return "email"
    if input_type == "tel":
        return "tel"
    raw = " ".join(
        attr(node, a) for a in ("name", "id", "placeholder", "aria-label", "autocomplete")
    )
    words = tokens(f"{raw} {label_text}")
    joined = "".join(words)
    for field_type, keywords in FIELD_KEYWORDS.items():
        if any(_keyword_hit(k, words, joined) for k in keywords):
            return field_type
    return None


def _label_text(node: LexborNode, labels: dict[str, str]) -> str:
    text = labels.get(attr(node, "id"), "")
    current = node.parent
    while current is not None and current.tag not in ("form", "body"):
        if current.tag == "label":
            text += " " + current.text(deep=True, separator=" ")
            break
        current = current.parent
    return text


def _data_inputs(form: LexborNode) -> list[LexborNode]:
    return [
        n
        for n in form.css("input, textarea, select")
        if attr(n, "type").lower() not in NON_DATA_INPUT_TYPES
    ]


def _is_search_form(inputs: list[LexborNode]) -> bool:
    text_inputs = [n for n in inputs if n.tag == "input" and attr(n, "type").lower() != "password"]
    if len(inputs) != 1 or len(text_inputs) != 1:
        return False
    node = text_inputs[0]
    return attr(node, "type").lower() == "search" or fold(attr(node, "name")) in SEARCH_NAMES


def _is_hidden(node: LexborNode) -> bool:
    style = attr(node, "style").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _has_honeypot(form: LexborNode) -> bool:
    for node in form.css("input"):
        if attr(node, "type").lower() == "hidden":
            continue
        classes = attr(node, "class").lower()
        parent_hidden = node.parent is not None and _is_hidden(node.parent)
        if (
            fold(attr(node, "name")) in HONEYPOT_NAMES
            or "honeypot" in classes
            or _is_hidden(node)
            or parent_hidden
        ):
            return True
    return False


def page_has_spam_protection(tree: LexborHTMLParser) -> bool:
    if any(tree.css_first(sel) is not None for sel in SPAM_SELECTORS):
        return True
    sources = [attr(n, "src").lower() for n in tree.css("script[src], iframe[src]")]
    return any(marker in src for src in sources for marker in SPAM_SCRIPT_MARKERS)


def personal_data_fields(form: LexborNode, labels: dict[str, str]) -> list[str]:
    """Formun topladığı kişisel veri alan TÜRLERİ (ad, e-posta, telefon...). Arama ve giriş
    (şifre + sadece e-posta) formlarında boş liste."""
    inputs = _data_inputs(form)
    if not inputs or _is_search_form(inputs):
        return []
    fields = sorted({f for n in inputs if (f := classify_field(n, _label_text(n, labels)))})
    has_password = bool(form.css('input[type="password"]'))
    if has_password and set(fields) <= {"email"}:
        return []  # giriş formu: yeni kişisel veri toplanmıyor
    return fields


def page_labels(tree: LexborHTMLParser) -> dict[str, str]:
    return {attr(n, "for"): n.text(deep=True, separator=" ") for n in tree.css("label[for]")}


def _form_entry(
    page_url: str, form: LexborNode, labels: dict[str, str], page_spam: bool
) -> dict[str, Any] | None:
    fields = personal_data_fields(form, labels)
    if not fields:
        return None
    action = urljoin(page_url, attr(form, "action") or page_url).split("?", 1)[0].split("#", 1)[0]
    has_consent = has_consent_near(form)
    has_spam = page_spam or _has_honeypot(form)
    insecure = action.lower().startswith("http://")
    issues = [
        name
        for name, bad in (
            ("insecure_action", insecure),
            ("no_consent", not has_consent),
            ("no_spam_protection", not has_spam),
        )
        if bad
    ]
    if not issues:
        return None
    return {
        "page": page_url,
        "fields": fields,
        "has_consent": has_consent,
        "has_spam_protection": has_spam,
        "action": action,
        "insecure_action": insecure,
        "issues": issues,
    }


def _form_severity(entry: dict[str, Any]) -> Severity:
    if entry["insecure_action"]:
        return Severity.HIGH
    if not entry["has_consent"]:
        return Severity.HIGH if SENSITIVE_FIELDS & set(entry["fields"]) else Severity.MEDIUM
    return Severity.LOW


@register
class FormWithoutConsent(WebCheck):
    id = "web.privacy.form_without_consent"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        forms: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for page, tree in iter_pages(ctx):
            labels = page_labels(tree)
            page_spam = page_has_spam_protection(tree)
            for form in tree.css("form"):
                entry = _form_entry(page.url, form, labels, page_spam)
                if entry is None:
                    continue
                # Her sayfada tekrar eden form (ör. footer bülteni) bir kez raporlanır.
                signature = (entry["action"], tuple(entry["fields"]), tuple(entry["issues"]))
                if signature not in seen:
                    seen.add(signature)
                    forms.append(entry)
        if not forms:
            return []
        severity = min((_form_severity(f) for f in forms), key=lambda s: s.rank)
        return [self.finding({"forms": forms[:MAX_FORMS], "count": len(forms)}, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        forms = evidence.get("forms") or []
        issues = {i for f in forms for i in f.get("issues") or []}
        if "insecure_action" in issues:
            headline = fragment("headline_insecure")
        elif "no_consent" in issues:
            headline = fragment("headline_no_consent")
        else:
            headline = fragment("headline_no_spam")
        details = "; ".join(fragment(f"issue_{i}") for i in sorted(issues))
        return {"headline": headline, "details": details}


def _parse_hosts(entries: list[str]) -> list[tuple[str, str]]:
    parsed = []
    for entry in entries:
        host, _, path = entry.lower().partition("/")
        parsed.append((host.lstrip("*."), "/" + path if path else ""))
    return parsed


def _match_provider(url: str, providers: list[tuple[str, str]]) -> tuple[str, str] | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for provider_host, path_prefix in providers:
        host_ok = host == provider_host or host.endswith("." + provider_host)
        if host_ok and parts.path.lower().startswith(path_prefix):
            return host, parts.path
    return None


def _first_segment(path: str) -> str:
    """Yolun ilk parçası; form kimliğine benziyorsa (rakam, uzun kod) saklanmaz."""
    segment = path.strip("/").split("/", 1)[0]
    if segment and segment.isalpha() and len(segment) <= FORM_ID_LIKE_MAX:
        return "/" + segment
    return ""


@register
class ThirdPartyFormEmbed(WebCheck):
    id = "web.privacy.third_party_form_embed"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        providers = _parse_hosts(get_settings().web_third_party_form_hosts)
        embeds: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        worst = Severity.INFO
        for page, tree in iter_pages(ctx):
            consent = page_has_consent(tree)
            for node in tree.css("iframe[src], script[src], a[href]"):
                raw = attr(node, "href" if node.tag == "a" else "src")
                match = _match_provider(urljoin(page.url, raw), providers)
                if match is None:
                    continue
                host, path = match
                kind = "link" if node.tag == "a" else node.tag
                if (page.url, host, kind) in seen:
                    continue
                seen.add((page.url, host, kind))
                embeds.append(
                    {
                        "page": page.url,
                        "provider": registered_domain(host),
                        "url_host": host + _first_segment(path),
                        "type": kind,
                        "page_has_consent": consent,
                    }
                )
                if kind != "link":
                    level = Severity.LOW if consent else Severity.MEDIUM
                    worst = min(worst, level, key=lambda s: s.rank)
        if not embeds:
            return []
        return [self.finding({"embeds": embeds[:MAX_EMBEDS], "count": len(embeds)}, severity=worst)]
