"""Faz 1 bulgularının Faz 2 verisiyle doğrulanması (PHASE2 §8.5).

Her kural: (bağlam, Faz 1 bulgusu) → Verification veya None (karar verilemedi →
`not_verifiable` kalır). Kurallar sadece ölçülen veriye dayanır.
"""

from collections.abc import Mapping
from urllib.parse import urlsplit

from launchcheck.db.models import Verification
from launchcheck.deep.context import DeepContext, ParentFinding
from launchcheck.deep.modules import VerificationRule
from launchcheck.deep.render_data import RenderedPage

RENDERED_CONTENT_FACTOR = 3  # render sonrası içerik sunucu HTML'inin 3 katından fazla
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "10.0.2.2", "::1"}  # noqa: S104
VIEWPORT_LAYOUT_ISSUES = {"missing", "fixed_width", "no_device_width"}


def _key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path.rstrip('/') or '/'}"


def _rendered(ctx: DeepContext) -> dict[str, RenderedPage]:
    if ctx.render is None:
        return {}
    return {_key(p.url): p for p in ctx.render.pages} | {
        _key(p.final_url): p for p in ctx.render.pages if p.final_url
    }


def empty_server_html(ctx: DeepContext, finding: ParentFinding) -> Verification | None:
    """İçerik tarayıcıda gerçekten oluşuyorsa sorun gerçek: botlar ilk (boş) hâli görür."""
    pages = _rendered(ctx)
    for entry in finding.evidence.get("pages") or []:
        page = pages.get(_key(str(entry.get("url", ""))))
        server_words = int(entry.get("main_content_words") or 0)
        if page is not None and page.main_words > RENDERED_CONTENT_FACTOR * max(server_words, 1):
            return Verification.CONFIRMED
    return None


def route_serves_root_shell(ctx: DeepContext, finding: ParentFinding) -> Verification | None:
    """Render sonrası title/h1/canonical sayfaya özgü hâle geliyorsa sorun gerçek: botlar ve
    paylaşım önizlemeleri ilk gelen ana sayfa HTML'ini görür."""
    pages = _rendered(ctx)
    home = ctx.render.home if ctx.render else None
    if home is None:
        return None
    for entry in finding.evidence.get("pages") or []:
        page = pages.get(_key(str(entry.get("url", ""))))
        if page is not None and (
            (page.title and page.title != home.title)
            or (page.h1 and page.h1 != home.h1)
            or (page.canonical and _key(page.canonical) != _key(home.canonical or home.url))
        ):
            return Verification.CONFIRMED
    return None


def og_missing(ctx: DeepContext, finding: ParentFinding) -> Verification | None:
    """Paylaşım botları JS çalıştırmaz: etiket render sonrası eklense bile sorun sürer."""
    return Verification.CONFIRMED if ctx.render and ctx.render.home else None


def sourcemap_exposed(ctx: DeepContext, finding: ParentFinding) -> Verification | None:
    if ctx.render and ctx.render.sourcemap_source_count:
        return Verification.ESCALATED
    return None


def viewport_missing(ctx: DeepContext, finding: ParentFinding) -> Verification | None:
    """Mobilde sayfa cihaz genişliğinde çiziliyorsa (ör. etiket JS ile ekleniyor) sorun yok."""
    issues = set(finding.evidence.get("issues") or [])
    home = ctx.render.home if ctx.render else None
    if not issues & VIEWPORT_LAYOUT_ISSUES or home is None or home.mobile is None:
        return None
    mobile = home.mobile
    return Verification.REFUTED if mobile.inner_width == mobile.width else Verification.CONFIRMED


def localhost_urls(ctx: DeepContext, finding: ParentFinding) -> Verification | None:
    """Sayfa gerçekten localhost'a istek atıyorsa daha ağır; kod yolu çalışmıyorsa yine gerçek."""
    if ctx.render is None:
        return None
    hit = any(host in LOCAL_HOSTS for host, _, _ in ctx.render.blocked_hosts)
    return Verification.ESCALATED if hit else Verification.CONFIRMED


RENDER_RULES: Mapping[str, VerificationRule] = {
    "web.render.empty_server_html": empty_server_html,
    "web.render.route_serves_root_shell": route_serves_root_shell,
    "web.meta.og_missing": og_missing,
    "web.files.sourcemap_exposed": sourcemap_exposed,
    "web.meta.viewport_missing": viewport_missing,
    "web.bundle.localhost_urls": localhost_urls,
}
