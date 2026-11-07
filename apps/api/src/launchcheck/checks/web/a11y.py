"""Erişilebilirlik: alt metni olmayan görseller."""

from urllib.parse import urljoin

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck
from launchcheck.checks.web._html import attr, iter_pages
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

MAX_SAMPLES = 10
MAX_SRC_CHARS = 200
TINY_DATA_URI_CHARS = 1024  # bundan kısa data: URI'ler (placeholder, piksel) sayılmaz
LAZY_SRC_ATTRS = ("src", "data-src", "data-lazy-src")


def _is_tracking_pixel(width: str, height: str) -> bool:
    return width.strip() in ("0", "1") or height.strip() in ("0", "1")


@register
class ImgMissingAlt(WebCheck):
    """alt attribute'u HİÇ olmayan görseller. alt="" dekoratif görsel sayılır (sorun değil).
    Aynı görsel (ör. her sayfadaki logo) bir kez sayılır."""

    id = "web.a11y.img_missing_alt"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        settings = get_settings()
        images: dict[str, bool] = {}  # görsel anahtarı → alt eksik mi
        samples: list[dict[str, str]] = []
        for page, tree in iter_pages(ctx):
            for index, node in enumerate(tree.css("img")):
                if _is_tracking_pixel(attr(node, "width"), attr(node, "height")):
                    continue
                src = next((attr(node, a) for a in LAZY_SRC_ATTRS if attr(node, a)), "")
                if src.startswith("data:") and len(src) < TINY_DATA_URI_CHARS:
                    continue
                key = urljoin(page.url, src) if src else f"{page.url}#img-{index}"
                missing = "alt" not in node.attributes
                if missing and not images.get(key, False) and len(samples) < MAX_SAMPLES:
                    samples.append({"page": page.url, "src": (src or "(src yok)")[:MAX_SRC_CHARS]})
                images[key] = images.get(key, False) or missing

        total = len(images)
        missing_count = sum(images.values())
        if not total or not missing_count:
            return []
        ratio = missing_count / total
        if (
            missing_count < settings.web_img_alt_min_missing
            and ratio <= settings.web_img_alt_max_ratio
        ):
            return []
        evidence = {"total_images": total, "missing_alt": missing_count, "samples": samples}
        return [self.finding(evidence)]
