"""og:image ve YouTube doğrulama yardımcıları (ağdan bağımsız; modül sonuçları buraya verir)."""

import io
import re
from urllib.parse import parse_qs, quote, urlsplit

from launchcheck.checks.web.links import NON_VIDEO_EMBEDS, YOUTUBE_HOSTS

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ID_PATH_PREFIXES = ("/embed/", "/shorts/", "/live/", "/v/")
# Paylaşım önizlemelerinde (WhatsApp, Facebook, LinkedIn, X) sorun çıkaran durumlar
MEDIUM_OG_ISSUES = frozenset(
    {"unreachable", "http_error", "not_image", "svg_unsupported", "too_large", "relative_url"}
)


def video_id(url: str) -> str | None:
    """Geçerli YouTube video kimliği; video linki değilse veya bozuksa None (bozuk linkler
    Faz 1'in `malformed_youtube` konusudur)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    path = parts.path
    candidate = ""
    if host == "youtu.be":
        candidate = path.strip("/").split("/", 1)[0]
    elif path.rstrip("/") == "/watch":
        candidate = (parse_qs(parts.query).get("v") or [""])[0]
    else:
        for prefix in ID_PATH_PREFIXES:
            if path.startswith(prefix):
                candidate = path[len(prefix) :].split("/", 1)[0]
                break
    # "videoseries" de 11 karakter: oynatma listesi gömmesi, video değil
    if candidate in NON_VIDEO_EMBEDS or not VIDEO_ID_RE.match(candidate):
        return None
    return candidate


def oembed_url(endpoint: str, vid: str) -> str:
    watch = f"https://www.youtube.com/watch?v={vid}"
    return f"{endpoint}?url={quote(watch, safe='')}&format=json"


def video_state(status: int | None) -> str:
    """oEmbed yanıtı: 200 var, 401/403 gömme kapalı veya özel, 404 silinmiş/bulunamadı."""
    if status == 200:
        return "ok"
    if status in (401, 403):
        return "not_embeddable"
    if status == 404:
        return "removed"
    return "unknown"


def image_size(data: bytes) -> tuple[int, int] | None:
    """Görselin piksel boyutu (sadece başlık okunur; kesilmiş dosyada da çalışır)."""
    if not data:
        return None
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None


def og_issues(
    raw_value: str,
    status: int | None,
    content_type: str,
    size_bytes: int | None,
    dims: tuple[int, int] | None,
    *,
    max_bytes: int,
    min_px: int,
    ratio_min: float,
    ratio_max: float,
) -> list[str]:
    issues: list[str] = []
    if not raw_value.lower().startswith(("http://", "https://")):
        # Paylaşım botlarının çoğu göreli adresi çözmez (Open Graph mutlak URL ister)
        issues.append("relative_url")
    if status is None:
        return [*issues, "unreachable"]
    if status != 200:
        return [*issues, "http_error"]
    if content_type == "image/svg+xml":
        return [*issues, "svg_unsupported"]
    if not content_type.startswith("image/"):
        return [*issues, "not_image"]
    if size_bytes is not None and size_bytes > max_bytes:
        issues.append("too_large")
    if dims is not None:
        width, height = dims
        if min(width, height) < min_px:
            issues.append("too_small")
        elif height and not ratio_min <= width / height <= ratio_max:
            issues.append("bad_ratio")
    return issues
