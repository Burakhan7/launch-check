"""Kullanıcı içeriklerinin gizliliği: hassas gönderilerin arama motorlarına açık olması ve
yüklenen görsellerde konum (EXIF GPS) bilgisinin kalması.

Gönderi metinleri ve GPS koordinatları kanıta/loglara YAZILMAZ; sadece URL ve "var" bilgisi.
"""

import asyncio
import warnings
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlsplit

from PIL import ExifTags, Image

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.domains import same_site
from launchcheck.checks.shared.text import fold, phrase_regex, squash, tokens
from launchcheck.checks.web._common import WebCheck, is_noindex, meta_content
from launchcheck.checks.web._html import attr, content_signals, iter_pages, page_text
from launchcheck.collectors.web_collector import HTTP_OK, WebContext
from launchcheck.collectors.web_signals import PageSignals
from launchcheck.config import get_settings

MAX_PAGES = 5
MAX_SIGNALS = 5
POST_PATH_SEGMENTS = 2  # /<kategori>/<slug>
SENSITIVE_WORDS = (
    "saglik",
    "iliski",
    "aile",
    "itiraf",
    "confession",
    "yalnizlik",
    "depresyon",
    "health",
    "relationship",
    "mental",
)
SENSITIVE_RE = phrase_regex(SENSITIVE_WORDS, prefix=True)
DESCRIPTION_PREFIX_CHARS = 60
MIN_DESCRIPTION_CHARS = 30


def _is_post_page(url: str, signals: PageSignals | None) -> bool:
    if signals is None:
        return False
    if signals.article:
        return True
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return len(segments) == POST_PATH_SEGMENTS and signals.comment_section


def _sensitive_signals(url: str, section: str, emotional: bool) -> list[str]:
    found = []
    if section and SENSITIVE_RE.search(fold(section)):
        found.append(f"section:{section}")
    path_words = tokens(urlsplit(url).path)
    if any(SENSITIVE_RE.match(w) for w in path_words):
        hit = next(w for w in path_words if SENSITIVE_RE.match(w))
        found.append(f"url:{hit}")
    if emotional:
        found.append("emotional_support_platform")
    return found


def description_copies_body(description: str, body_text: str) -> bool:
    """Açıklamanın ilk 60 karakteri sayfa gövdesindeki metinde birebir geçiyor mu?"""
    head = squash(fold(description)).rstrip(".… ")[:DESCRIPTION_PREFIX_CHARS]
    return len(head) >= MIN_DESCRIPTION_CHARS and head in body_text


@register
class SensitiveUgcIndexed(WebCheck):
    id = "web.privacy.sensitive_ugc_indexed"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        signals = content_signals(ctx)
        if not signals.is_ugc:
            return []
        by_url = {p.url: p for p in signals.pages}
        pages: list[str] = []
        sensitive: list[str] = []
        full_text = False
        for page, tree in iter_pages(ctx):
            if not _is_post_page(page.url, by_url.get(page.url)) or is_noindex(page, tree):
                continue
            section = meta_content(tree, prop="article:section")
            hits = _sensitive_signals(page.url, section, signals.is_emotional_support)
            if not hits:
                continue
            pages.append(page.url)
            sensitive += [h for h in hits if h not in sensitive]
            description = meta_content(tree, name="description") or meta_content(
                tree, prop="og:description"
            )
            if description and description_copies_body(description, page_text(ctx, page)):
                full_text = True
        if not pages:
            return []
        evidence: dict[str, Any] = {
            "pages": pages[:MAX_PAGES],
            "indexable": True,
            "full_text_in_meta": full_text,
            "sensitive_signals": sensitive[:MAX_SIGNALS],
        }
        return [self.finding(evidence)]


# --- EXIF

MAX_GPS_URLS = 3
IMAGE_FETCH_CONCURRENCY = 4
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "jpe", "heic", "heif", "tif", "tiff", "webp"}
OTHER_IMAGE_EXTENSIONS = {"png", "gif", "svg", "avif", "ico", "bmp", "apng"}
GPS_POSITION_TAGS = (2, 4)  # GPSLatitude, GPSLongitude
DEVICE_TAGS = (0x010F, 0x0110)  # Make, Model (IFD0)
DEVICE_EXIF_TAGS = (0xA431, 0x9003)  # BodySerialNumber, DateTimeOriginal (Exif IFD)
EXIF_HEADER = b"Exif\x00\x00"
HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1")
FTYP_OFFSET = 4
FTYP_BRAND_END = 12
WEBP_TAG_OFFSET = 8
WEBP_TAG_END = 12


def image_kind(data: bytes) -> str | None:
    """Sihirli baytlara göre tür; desteklenmeyen/görsel olmayan → None."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if data.startswith(b"RIFF") and data[WEBP_TAG_OFFSET:WEBP_TAG_END] == b"WEBP":
        return "webp"
    if data[FTYP_OFFSET:8] == b"ftyp" and data[8:FTYP_BRAND_END] in HEIF_BRANDS:
        return "heic"
    return None


def _load_exif(data: bytes, kind: str) -> Image.Exif:
    if kind == "heic":  # Pillow HEIC açamaz; gömülü EXIF bloğu doğrudan okunur
        exif = Image.Exif()
        start = data.find(EXIF_HEADER)
        if start >= 0:
            exif.load(data[start:])
        return exif
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # DecompressionBombWarning: pikseller zaten çözülmüyor
        with Image.open(BytesIO(data)) as img:
            return img.getexif()


def exif_flags(data: bytes) -> tuple[bool, bool] | None:
    """(GPS konumu var, cihaz/çekim bilgisi var). Okunamazsa None. Değerler döndürülmez."""
    kind = image_kind(data)
    if kind is None:
        return None
    try:
        exif = _load_exif(data, kind)
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        sub = exif.get_ifd(ExifTags.IFD.Exif)
    except Exception:  # bozuk/kesik dosya: kontrol edilemedi
        return None
    has_gps = any(gps.get(tag) for tag in GPS_POSITION_TAGS)
    has_device = any(exif.get(t) for t in DEVICE_TAGS) or any(sub.get(t) for t in DEVICE_EXIF_TAGS)
    return has_gps, has_device


def _extension(path: str) -> str:
    last = path.rsplit("/", 1)[-1]
    return last.rsplit(".", 1)[-1].lower() if "." in last else ""


def is_user_upload(url: str, site_host: str) -> bool:
    settings = get_settings()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or not same_site(host, site_host):
        return False
    path = parts.path.lower()
    if any(m in path for m in settings.web_exif_skip_path_markers):
        return False
    if not any(m in path for m in settings.web_exif_upload_path_markers):
        return False
    ext = _extension(path)
    return ext in ALLOWED_EXTENSIONS or (ext not in OTHER_IMAGE_EXTENSIONS and not ext)


def _image_sources(ctx: WebContext) -> list[str]:
    found: list[str] = []
    for page, tree in iter_pages(ctx):
        raw = [attr(n, "src") or attr(n, "data-src") for n in tree.css("img")]
        raw += [
            attr(n, "srcset").split(",", 1)[0].strip().split(" ", 1)[0]
            for n in tree.css("img[srcset], source[srcset]")
        ]
        raw.append(meta_content(tree, prop="og:image"))
        for src in raw:
            if src and not src.startswith("data:"):
                url = urljoin(page.url, src.strip())
                if url not in found:
                    found.append(url)
    return found


def user_images(ctx: WebContext) -> list[str]:
    site_host = (urlsplit(ctx.final_url).hostname or "").lower()
    limit = get_settings().web_exif_max_images
    return [u for u in _image_sources(ctx) if is_user_upload(u, site_host)][:limit]


@register
class ExifInUploads(WebCheck):
    id = "web.privacy.exif_in_uploads"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def _inspect(self, ctx: WebContext, url: str, sem: asyncio.Semaphore) -> Any:
        async with sem:
            res = await ctx.fetch_uncached(url, get_settings().web_exif_max_image_bytes)
        if res is None or res.status != HTTP_OK:
            return None
        flags = exif_flags(res.body)  # gövde bellekte işlenir, diske yazılmaz ve saklanmaz
        return flags

    async def check(self, ctx: WebContext) -> list[Finding]:
        upload = content_signals(ctx).has_media_upload
        images = user_images(ctx)
        if not images and not upload:
            return []
        sem = asyncio.Semaphore(IMAGE_FETCH_CONCURRENCY)
        results = await asyncio.gather(*(self._inspect(ctx, u, sem) for u in images))
        checked = [(u, r) for u, r in zip(images, results, strict=True) if r is not None]
        if not checked:
            if upload:
                return [self.finding({"images_checked": 0}, severity=Severity.INFO)]
            return []
        with_gps = [u for u, (gps, _) in checked if gps]
        with_device = sum(1 for _, (_, device) in checked if device)
        if not with_gps and not with_device:
            return []
        evidence = {
            "images_checked": len(checked),
            "with_gps": with_gps[:MAX_GPS_URLS],
            "with_device_info": with_device,
        }
        return [self.finding(evidence, severity=Severity.HIGH if with_gps else Severity.LOW)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        if not evidence.get("images_checked"):
            key = "unchecked"
        elif evidence.get("with_gps"):
            key = "gps"
        else:
            key = "device"
        return {"headline": fragment(f"headline_{key}"), "situation": fragment(f"situation_{key}")}
