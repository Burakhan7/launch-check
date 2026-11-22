"""Lighthouse filmstrip'i: boş ekran süresi ve rapordaki kare şeridi.

Lighthouse (simüle kısıtlama) metrikleri yavaş 4G + yavaş CPU için *hesaplar*; ekran
görüntüsü kareleri ise gerçekte gözlenen, kısıtlamasız zaman çizelgesindedir. İki zaman
çizelgesini birbirine eşlemeyiz (simülasyon zamanlayıcıları/JS beklemelerini hesaba katmaz,
eşleme hızlı sayfalarda kareleri şişirir). Boş ekran süresi iki alt sınırın büyüğüdür:
simüle FCP (ağ/CPU yavaşlığı) ve gözlenen boş süre (zamanlayıcı/JS beklemesi). İkisi de
gerçek mobil deneyimin altında kalır; kuşkulu durumda bulgu üretmeyiz (docs/decisions.md, P4).
Şeritteki kareler gözlenen zamanlarıyla gösterilir.
"""

import base64
import io
from typing import Any

from PIL import Image

PIXEL_DIFF = 24  # baskın renkten bu kadar farklı piksel "içerik" sayılır (0-255 gri)
SAMPLE_SIZE = (60, 120)
FRAME_WIDTH = 144  # şeritteki kare genişliği; 10 kare ≤ 1440 px
MAX_FRAMES = 10
WEBP_QUALITY = 70


def decode_frames(items: list[dict[str, Any]]) -> list[tuple[float, bytes]]:
    """`screenshot-thumbnails` kareleri → (gözlenen ms, JPEG)."""
    frames = []
    for item in items:
        data, timing = item.get("data"), item.get("timing")
        if not isinstance(data, str) or not isinstance(timing, int | float) or "," not in data:
            continue
        try:
            frames.append((float(timing), base64.b64decode(data.split(",", 1)[1])))
        except ValueError:
            continue
    return frames


def content_ratio(jpeg: bytes) -> float:
    """Karede baskın renkten belirgin şekilde farklı piksellerin oranı (boş sayfa ≈ 0)."""
    with Image.open(io.BytesIO(jpeg)) as img:
        gray = img.convert("L").resize(SAMPLE_SIZE)
    histogram = gray.histogram()
    mode = max(range(256), key=histogram.__getitem__)
    total = SAMPLE_SIZE[0] * SAMPLE_SIZE[1]
    differing = sum(n for value, n in enumerate(histogram) if abs(value - mode) > PIXEL_DIFF)
    return differing / total


def blank_until(
    frames: list[tuple[float, bytes]], fcp_ms: float | None, min_ratio: float
) -> float | None:
    """Boş ekran süresi (ms): max(simüle FCP, gözlenen ilk anlamlı kareye kadar geçen süre).
    Tutucu: içerik iki kare arasında gelmişse önceki karenin zamanı alınır."""
    if not frames:
        return fcp_ms
    previous = 0.0
    end = frames[-1][0]
    for at, jpeg in frames:
        try:
            ratio = content_ratio(jpeg)
        except (OSError, ValueError):
            continue
        if ratio >= min_ratio:
            end = previous
            break
        previous = at
    return max(end, fcp_ms or 0.0)


def strip_webp(
    frames: list[tuple[float, bytes]], max_ms: float
) -> tuple[bytes, int, int, list[int]]:
    """İlk `max_ms` içindeki kareleri yan yana tek WebP görsele dizer.
    Dönüş: (webp, genişlik, yükseklik, kare zamanları ms)."""
    chosen = [f for f in frames if f[0] <= max_ms][:MAX_FRAMES] or frames[:1]
    images = []
    for _, jpeg in chosen:
        with Image.open(io.BytesIO(jpeg)) as img:
            ratio = FRAME_WIDTH / img.width
            images.append(img.convert("RGB").resize((FRAME_WIDTH, round(img.height * ratio))))
    height = max(i.height for i in images)
    strip = Image.new("RGB", (FRAME_WIDTH * len(images), height), "white")
    for index, img in enumerate(images):
        strip.paste(img, (index * FRAME_WIDTH, 0))
    buf = io.BytesIO()
    strip.save(buf, "WEBP", quality=WEBP_QUALITY)
    return buf.getvalue(), strip.width, strip.height, [round(t) for t, _ in chosen]
