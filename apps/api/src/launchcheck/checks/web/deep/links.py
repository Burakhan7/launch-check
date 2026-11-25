"""Link, meta ve yapılandırılmış veri check'leri (PHASE2 §6.4). Veri: `ctx.links`.

Modül bir parçayı tamamlayamadıysa (servis hatası, zaman aşımı) o parçanın check'i hiç
çalışmaz ve "geçti" listesine girmez.
"""

from abc import abstractmethod
from typing import Any, ClassVar
from urllib.parse import urlsplit

from launchcheck.checks.base import (
    Category,
    Check,
    Finding,
    FragmentFn,
    ScanContext,
    Severity,
    TargetType,
)
from launchcheck.checks.registry import register
from launchcheck.deep.context import DeepContext
from launchcheck.deep.links.media import MEDIUM_OG_ISSUES
from launchcheck.deep.links_data import LinksData, LinkStatus

MAX_ITEMS = 15


class LinksCheck(Check):
    target = TargetType.WEB
    phase = 2
    module: ClassVar[str | None] = "links"

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, DeepContext):
            raise TypeError("DeepContext bekleniyordu")
        if ctx.links is None:
            return []
        return await self.check(ctx, ctx.links)

    @abstractmethod
    async def check(self, ctx: DeepContext, data: LinksData) -> list[Finding]: ...


def _link_item(link: LinkStatus, own_site: str) -> dict[str, Any]:
    url = link.url
    if url.startswith(own_site):
        url = urlsplit(url).path or "/"
    return {
        "url": url,
        "status": link.status,
        "reason": link.reason,
        "pages": link.pages,
        "in_menu": link.prominent,
    }


def _count(evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
    return {"count": str(evidence.get("count", 0))}


@register
class BrokenInternalLinks(LinksCheck):
    """Render'ın gezdiği sayfalardaki iç linkler: 404/410/5xx. Render'ın zaten açtığı sayfaların
    durumu tekrar istenmez. SPA rotası tıklayınca açılıyorsa 404'ler spa_deep_link_404'ün."""

    id = "web.links.broken_internal"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: LinksData) -> list[Finding]:
        if not data.internal:
            return []
        links = sorted(data.internal, key=lambda link: (not link.prominent, link.url))
        evidence = {
            "links": [_link_item(link, data.site) for link in links[:MAX_ITEMS]],
            "count": len(links),
            "checked": data.internal_checked,
            "source": data.source,
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {**_count(evidence, fragment), "checked": str(evidence.get("checked", 0))}


@register
class BrokenExternalLinks(LinksCheck):
    id = "web.links.broken_external"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: LinksData) -> list[Finding]:
        if not data.external:
            return []
        links = sorted(data.external, key=lambda link: (not link.prominent, link.url))
        evidence = {
            "links": [_link_item(link, data.site) for link in links[:MAX_ITEMS]],
            "count": len(links),
            "checked": data.external_checked,
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {**_count(evidence, fragment), "checked": str(evidence.get("checked", 0))}


@register
class HttpTargetNoHttps(LinksCheck):
    """Faz 1 `insecure_http_links` hedefleri HTTPS'i destekliyor mu? Faz 1 bulgusu zaten skora
    girdiği için bu bulgu bilgi niteliğindedir (önem `info`); Faz 1 bulgusunun doğrulama etiketi
    buradan gelir: hepsi destekliyorsa `confirmed` ("sadece linki düzelt"), bir kısmında SSL
    yoksa `escalated` ("hedef sitede SSL yok")."""

    id = "web.links.http_target_no_https"
    category = Category.SECURITY
    default_severity = Severity.INFO

    async def check(self, ctx: DeepContext, data: LinksData) -> list[Finding]:
        decided = [t for t in data.https_targets if t.supports_https is not None]
        if not decided:
            return []
        fixable = [t for t in decided if t.supports_https]
        no_ssl = [t for t in decided if not t.supports_https]
        evidence = {
            "fixable": [{"host": t.host, "https_url": t.url} for t in fixable][:MAX_ITEMS],
            "no_https": [{"host": t.host, "reason": t.reason} for t in no_ssl][:MAX_ITEMS],
            "fixable_count": len(fixable),
            "no_https_count": len(no_ssl),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        no_https = int(evidence.get("no_https_count") or 0)
        fixable = int(evidence.get("fixable_count") or 0)
        name = "some_without_ssl" if no_https else "all_support_https"
        return {
            "fixable": str(fixable),
            "no_https": str(no_https),
            "summary": fragment(name, fixable=fixable, no_https=no_https),
        }


@register
class YoutubeUnavailable(LinksCheck):
    """Silinmiş/özel videolar ve sayfada gömülü olduğu hâlde gömmesi kapatılmış videolar.
    Sadece link verilen videonun gömmesinin kapalı olması sorun değildir."""

    id = "web.links.youtube_unavailable"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: LinksData) -> list[Finding]:
        bad = [
            v
            for v in data.videos
            if v.state == "removed" or (v.state == "not_embeddable" and v.embedded)
        ]
        if not bad:
            return []
        evidence = {
            "videos": [
                {"video_id": v.video_id, "page": v.page, "state": v.state, "embedded": v.embedded}
                for v in bad[:MAX_ITEMS]
            ],
            "count": len(bad),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return _count(evidence, fragment)


@register
class OgImageUnreachable(LinksCheck):
    """og:image / twitter:image gerçekten yükleniyor mu, görsel mi, boyut/oran uygun mu?
    Paylaşım botları JS çalıştırmadığı için önce sunucu HTML'indeki etiket kullanılır."""

    id = "web.meta.og_image_unreachable"
    category = Category.CONVERSION
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: LinksData) -> list[Finding]:
        bad = [img for img in data.og_images if img.issues]
        if not bad:
            return []
        medium = any(set(img.issues) & MEDIUM_OG_ISSUES for img in bad)
        evidence = {
            "images": [
                {
                    "page": img.page,
                    "tag": img.tag,
                    "url": img.url,
                    "source": img.source,
                    "status": img.status,
                    "content_type": img.content_type,
                    "bytes": img.bytes,
                    "size": [img.width, img.height] if img.width else None,
                    "issues": img.issues,
                }
                for img in bad[:MAX_ITEMS]
            ],
            "count": len(bad),
        }
        return [self.finding(evidence, Severity.MEDIUM if medium else Severity.LOW)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        issues: list[str] = []
        for image in evidence.get("images") or []:
            for issue in image.get("issues") or []:
                if issue not in issues:
                    issues.append(issue)
        return {"issues": ", ".join(fragment(f"issue_{i}") for i in issues[:4])}


@register
class StructuredDataInvalid(LinksCheck):
    id = "web.seo.structured_data_invalid"
    category = Category.CONVERSION
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: LinksData) -> list[Finding]:
        pages = [p for p in data.jsonld if p.issues]
        if not pages:
            return []
        evidence = {
            "pages": [
                {
                    "page": p.page,
                    "types": p.types[:10],
                    "issues": [{k: v for k, v in vars(i).items() if v} for i in p.issues],
                }
                for p in pages[:MAX_ITEMS]
            ],
            "count": sum(len(p.issues) for p in pages),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return _count(evidence, fragment)


@register
class StructuredDataOpportunity(LinksCheck):
    """Ürün (fiyat + sepete ekle), SSS (en az 3 soru başlığı + SSS başlığı/akordeon) veya yerel
    işletme (harita + telefon/adres) sinyali var ama sitede o türde yapılandırılmış veri yok."""

    id = "web.seo.structured_data_opportunity"
    category = Category.CONVERSION
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: LinksData) -> list[Finding]:
        if not data.opportunities:
            return []
        evidence = {
            "opportunities": [
                {"kind": o.kind, "page": o.page, "signals": o.signals} for o in data.opportunities
            ],
            "present_types": data.present_types[:10],
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        kinds = [str(o.get("kind")) for o in evidence.get("opportunities") or []]
        return {"kinds": ", ".join(fragment(f"kind_{k}") for k in kinds)}
