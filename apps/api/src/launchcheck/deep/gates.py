"""Rapor için tarama sonu özetleri (orkestratör, modüllerden sonra; PHASE2 §8.3, §8.9).

- `gates`: Faz 2'nin topladığı ama KASITLI OLARAK test etmediği yüzeyler (Faz 3 köprüsü).
  Sitenin render sırasında kendi attığı API istekleri, Supabase/Firebase projeleri ve kodda adı
  geçen tablo/bucket adları, kimlik doğrulama ve webhook yolları, kişisel veri toplayan formlar,
  doğrulanamamış yönetim rotaları. Bu adreslere hiçbir istek atılmaz; sadece listelenir.
- `preview`: "Gördüğün vs. gördükleri" kartları için başlık/açıklama/og verisi (önce sunucu
  HTML'i: paylaşım botları ve Google JS'siz ilk hâli görür).
Yollardaki kimlikler `{id}` ile değiştirilir; sorgu dizesi tutulmaz.
"""

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.shared.domains import registered_domain
from launchcheck.deep.context import DeepContext

MAX_ENDPOINTS = 30
MAX_NAMES = 20
ID_SEGMENT_RE = re.compile(
    r"^(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,}|"
    r"[A-Za-z0-9_-]{20,})$",
    re.IGNORECASE,
)
TABLE_RE = re.compile(r"(?<!storage)\.from\(\s*[\"'`]([A-Za-z0-9_-]{1,63})[\"'`]\s*\)")
BUCKET_RE = re.compile(r"storage\s*\.from\(\s*[\"'`]([A-Za-z0-9_-]{1,63})[\"'`]\s*\)")
AUTH_RE = re.compile(r"auth|login|signin|sign-in|signup|register|token|oauth|session|otp|magic")
WEBHOOK_RE = re.compile(r"webhook|/hooks?/|callback")
WEBHOOK_PATH_RE = re.compile(r"[\"'`](/api/[A-Za-z0-9_/.-]*(?:webhook|hook)[A-Za-z0-9_/.-]*)[\"'`]")
BACKEND_SUFFIXES = (".supabase.co", ".firebaseio.com", "firestore.googleapis.com")
FORM_CHECKS = (
    "web.privacy.form_without_consent",
    "web.privacy.special_category_without_consent",
    "web.privacy.third_party_form_embed",
)
STATIC_EXT = (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2", ".webp", ".ico")


def template_path(path: str) -> str:
    parts = [("{id}" if ID_SEGMENT_RE.match(p) else p) for p in path.split("/")]
    return "/".join(parts) or "/"


def _endpoints(ctx: DeepContext) -> list[dict[str, str]]:
    seen: dict[tuple[str, str, str], dict[str, str]] = {}
    for page in ctx.render.pages if ctx.render else []:
        for method, url in page.api_calls:
            parts = urlsplit(url)
            if parts.path.lower().endswith(STATIC_EXT):
                continue
            key = (method, parts.netloc, template_path(parts.path))
            seen.setdefault(key, {"method": method, "host": parts.netloc, "path": key[2]})
    site = registered_domain(urlsplit(ctx.target_url).hostname or "")
    own = [e for e in seen.values() if registered_domain(e["host"].split(":")[0]) == site]
    backend = [e for e in seen.values() if e["host"].endswith(BACKEND_SUFFIXES)]
    rest = [e for e in seen.values() if e not in own and e not in backend]
    return [*own, *backend, *rest][:MAX_ENDPOINTS]


def _bundle_texts(ctx: DeepContext) -> list[str]:
    return [s.content for s in ctx.base.scripts] if ctx.base is not None else []


def _names(regex: re.Pattern[str], texts: list[str]) -> list[str]:
    found: list[str] = []
    for text in texts:
        for match in regex.finditer(text):
            name = match.group(1)
            if name not in found:
                found.append(name)
            if len(found) >= MAX_NAMES:
                return found
    return found


def _forms(ctx: DeepContext) -> list[str]:
    pages: list[str] = []
    for finding in ctx.parent_findings:
        if finding.check_id not in FORM_CHECKS:
            continue
        for item in finding.evidence.get("forms") or finding.evidence.get("embeds") or []:
            page = str(item.get("page") or "") if isinstance(item, dict) else ""
            path = urlsplit(page).path or "/"
            if page and path not in pages:
                pages.append(path)
    signup = ctx.funnel.signup if ctx.funnel is not None else None
    if signup is not None and signup.page not in pages:
        pages.append(signup.page)
    return pages[:MAX_NAMES]


def _admin_unverified(ctx: DeepContext) -> list[str]:
    if ctx.render is None:
        return []
    return [
        p.path
        for p in ctx.render.admin
        if p.signals and not p.login_form and not p.redirected_to_login and p.status in (200, None)
    ][:MAX_NAMES]


def build_gates(ctx: DeepContext) -> dict[str, Any]:
    endpoints = _endpoints(ctx)
    texts = _bundle_texts(ctx)
    meta = ctx.parent_meta
    supabase = list(
        dict.fromkeys(
            [
                *(str(u) for u in meta.get("supabase_urls") or []),
                *(f"https://{e['host']}" for e in endpoints if e["host"].endswith(".supabase.co")),
            ]
        )
    )
    firebase = list(dict.fromkeys(str(p) for p in meta.get("firebase_project_ids") or []))
    tables = _names(TABLE_RE, texts) if supabase else []
    buckets = _names(BUCKET_RE, texts) if supabase else []
    auth = [f"{e['method']} {e['path']}" for e in endpoints if AUTH_RE.search(e["path"].lower())]
    hooks = [
        f"{e['method']} {e['path']}" for e in endpoints if WEBHOOK_RE.search(e["path"].lower())
    ]
    hooks += [p for p in _names(WEBHOOK_PATH_RE, texts) if p not in hooks]
    gates = {
        "api_endpoints": endpoints,
        "supabase": {"projects": supabase[:5], "tables": tables, "buckets": buckets},
        "firebase": {"projects": firebase[:5]},
        "auth_endpoints": auth[:MAX_NAMES],
        "webhooks": hooks[:MAX_NAMES],
        "personal_data_forms": _forms(ctx),
        "admin_unverified": _admin_unverified(ctx),
    }
    gates["total"] = (
        len(endpoints)
        + len(gates["supabase"]["projects"])
        + len(firebase)
        + len(gates["webhooks"])
        + len(gates["personal_data_forms"])
        + len(gates["admin_unverified"])
    )
    return gates


def _meta(tree: LexborHTMLParser, *names: str) -> str:
    for name in names:
        node = tree.css_first(f'meta[property="{name}"], meta[name="{name}"]')
        value = (node.attributes.get("content") or "").strip() if node else ""
        if value:
            return value[:300]
    return ""


def build_preview(ctx: DeepContext) -> dict[str, Any]:
    """Önce sunucu HTML'i (botların gördüğü), yoksa render sonrası değerler."""
    home = ctx.render.home if ctx.render else None
    url = (home.final_url or home.url) if home else ctx.target_url.rstrip("/") + "/"
    if ctx.base is not None and ctx.base.pages:
        page = ctx.base.pages[0]
        tree = LexborHTMLParser(page.html)
        title_node = tree.css_first("title")
        og_image = _meta(tree, "og:image", "twitter:image")
        return {
            "source": "server",
            "url": page.url,
            "title": (title_node.text().strip() if title_node else "")[:200],
            "description": _meta(tree, "description"),
            "og_title": _meta(tree, "og:title"),
            "og_description": _meta(tree, "og:description"),
            "og_image": urljoin(page.url, og_image) if og_image else "",
        }
    og = home.og if home else {}
    return {
        "source": "rendered",
        "url": url,
        "title": home.title if home else "",
        "description": "",
        "og_title": og.get("og:title", ""),
        "og_description": og.get("og:description", ""),
        "og_image": urljoin(url, og.get("og:image", "")) if og.get("og:image") else "",
    }


def finalize(ctx: DeepContext) -> None:
    """Orkestratör, modüller bitince ve skorlamadan önce çağırır."""
    ctx.meta["gates"] = build_gates(ctx)
    ctx.meta["preview"] = build_preview(ctx)
