"""Her ziyaretçiye (botlar dahil) otomatik misafir hesap/oturum açılması.

Collector ana sayfayı çerez göndermeden birkaç kez çeker (`cookieless_samples`). Sadece görünen
link metinleri ve meta/inline JS işaretleri karşılaştırılır; CSRF/nonce gibi gizli değerler ve
çerez DEĞERLERİ kanıta yazılmaz (sadece çerez adları).
"""

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.text import squash
from launchcheck.checks.web._common import WebCheck
from launchcheck.checks.web._html import attr
from launchcheck.collectors.web_collector import PageSnapshot, WebContext

MAX_IDENTITIES = 3
MAX_COOKIES = 5
MIN_SAMPLES = 2
MAX_LINK_TEXT_CHARS = 60
AUTH_META_NAMES = {"auth", "logged-in", "logged_in", "loggedin", "user-id", "user_id", "userid"}
AUTH_INLINE_RE = re.compile(
    r"""isLoggedIn["']?\s*[:=]\s*(?:true|!0)"""
    r"""|["']?\buser["']?\s*:\s*\{\s*["']?id["']?\s*:"""
    r"""|currentUser\s*=\s*\{""",
)
PROFILE_PATHS = ("/ben", "/me", "/profile", "/profil", "/account", "/hesap")
PROFILE_PREFIXES = ("/u/", "/user/", "/users/", "/profile/", "/profil/")
# Görünen kullanıcı adı: harf + rakam, boşluksuz, makul uzunlukta (ör. "AAnonim4227")
IDENTITY_RE = re.compile(r"^(?=.*\d)[^\W\d_][\w.-]{2,30}$")
# CSRF çerezleri (XSRF-TOKEN gibi) her istekte değişebilir; oturum çerezi sayılmaz.
SESSION_COOKIE_RE = re.compile(r"sess|(?:^|[._-])sid$", re.IGNORECASE)
SET_COOKIE_NAME_RE = re.compile(r"(?:^|,\s*)([A-Za-z0-9_.\-]+)=")


def _truthy(value: str) -> bool:
    value = value.strip().lower()
    return value in ("1", "true", "yes") or (value.isdigit() and value != "0")


def has_auth_marker(tree: LexborHTMLParser) -> bool:
    """`<meta name="auth" content="1">` veya inline JS'te giriş yapmış kullanıcı nesnesi."""
    for node in tree.css("meta[name]"):
        if attr(node, "name").lower() in AUTH_META_NAMES and _truthy(attr(node, "content")):
            return True
    for script in tree.css("script"):
        if attr(script, "src"):
            continue
        if AUTH_INLINE_RE.search(script.text(deep=True) or ""):
            return True
    return False


def _is_profile_path(href: str, base: str) -> bool:
    parts = urlsplit(urljoin(base, href))
    if parts.netloc != urlsplit(base).netloc:
        return False
    path = parts.path.rstrip("/").lower() or "/"
    return path in PROFILE_PATHS or path.startswith(PROFILE_PREFIXES)


def visible_identity(tree: LexborHTMLParser, base: str) -> str | None:
    """Profil linkinin görünen metni kullanıcı adı gibiyse onu döndürür."""
    for node in tree.css("a[href]"):
        if not _is_profile_path(attr(node, "href"), base):
            continue
        # Avatar harfi gibi ayrı öğeler metne karışır ("A Anonim9371"): parçalara bakılır.
        text = squash(node.text(deep=True, separator=" "))
        if len(text) > MAX_LINK_TEXT_CHARS:
            continue
        for part in text.split(" "):
            if IDENTITY_RE.match(part):
                return part
    return None


def session_cookie_names(sample: PageSnapshot) -> dict[str, str]:
    """Oturum çerezi adı → değer (değer sadece karşılaştırma için; kanıta yazılmaz)."""
    raw = sample.headers.get("set-cookie", "")
    out: dict[str, str] = {}
    for match in SET_COOKIE_NAME_RE.finditer(raw):
        name = match.group(1)
        if SESSION_COOKIE_RE.search(name):
            value = raw[match.end() :].split(";", 1)[0]
            out.setdefault(name, value)
    return out


def rotating_cookies(samples: list[PageSnapshot]) -> list[str]:
    """Her yanıtta farklı değerle gelen oturum çerezlerinin adları."""
    per_sample = [session_cookie_names(s) for s in samples]
    names = set.intersection(*(set(c) for c in per_sample)) if per_sample else set()
    return sorted(n for n in names if len({c[n] for c in per_sample}) == len(per_sample))


@register
class GuestAccountPerRequest(WebCheck):
    id = "web.session.guest_account_per_request"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        samples = ctx.cookieless_samples
        if len(samples) < MIN_SAMPLES:
            return []
        trees = [LexborHTMLParser(s.html) for s in samples]
        auth = any(has_auth_marker(t) for t in trees)
        identities = [visible_identity(t, s.url) for t, s in zip(trees, samples, strict=True)]
        rotating = all(identities) and len(set(identities)) == len(identities)
        if not (auth or rotating):
            return []
        signals = [
            name for name, hit in (("meta_auth", auth), ("rotating_identity", rotating)) if hit
        ]
        cookies = rotating_cookies(samples)
        if cookies:
            signals.append("rotating_session_cookie")
        evidence: dict[str, Any] = {
            "signals": signals,
            "sample_identities": [i for i in identities if i][:MAX_IDENTITIES] if rotating else [],
            "session_cookie_names": cookies[:MAX_COOKIES],
            "sample_count": len(samples),
        }
        return [self.finding(evidence, severity=Severity.MEDIUM if rotating else Severity.LOW)]
