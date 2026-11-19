"""Sayfa içinde çalışan ölçüm betikleri ve sonuçlarının ayrıştırılması.

Betikler sadece ölçüm döndürür: storage DEĞERLERİ sayfadan hiç çıkmaz (sadece anahtar adı,
uzunluk ve JWT bitiş süresi), form alanlarının içeriği okunmaz.
"""

from importlib import resources
from typing import Any

from launchcheck.deep.render_data import (
    Box,
    BrokenImage,
    FirstScreenItem,
    JunkText,
    StorageEntry,
    ViewportMetrics,
)


def _script(name: str) -> str:
    """`js/<name>` içeriği; baştaki `//` yorum satırları atılır (evaluate fonksiyon ister)."""
    text = resources.files("launchcheck.deep.browser").joinpath("js", name).read_text("utf-8")
    lines = text.splitlines()
    while lines and lines[0].lstrip().startswith("//"):
        lines.pop(0)
    return "\n".join(lines)


# Ortak yardımcılar + ölçüm. `opts`: {mobile: bool, full: bool}
MEASURE_JS = _script("measure.js")

# Storage ölçümü: değerler sayfada kalır, sadece ad/uzunluk/JWT bitişi döner.
STORAGE_JS = _script("storage.js")
# Yüklenen aynı origin JS dosyalarındaki yönetim rotası adayları (sadece yol şablonları)
ROUTES_JS = _script("routes.js")


def _boxes(items: Any) -> list[Box]:
    return [Box.from_js(i) for i in items or [] if isinstance(i, dict)]


def parse_metrics(data: dict[str, Any], width: int, height: int) -> ViewportMetrics:
    overlaps = [
        (Box.from_js(a), Box.from_js(b))
        for pair in data.get("overlaps") or []
        if isinstance(pair, list) and len(pair) == 2
        for a, b in [pair]
    ]
    cta = data.get("primaryCta")
    return ViewportMetrics(
        width=width,
        height=height,
        inner_width=int(data.get("innerWidth") or width),
        scroll_width=int(data.get("scrollWidth") or width),
        overflowing=_boxes(data.get("overflowing")),
        overlaps=overlaps,
        small_targets=_boxes(data.get("smallTargets")),
        primary_cta=Box.from_js(cta) if isinstance(cta, dict) else None,
        first_screen=[
            FirstScreenItem(
                kind=str(i.get("kind", "")),
                text=str(i.get("text", ""))[:80],
                position=str(i.get("position", "")),
                size_pct=float(i.get("size_pct") or 0),
                primary_cta=bool(i.get("primary_cta")),
            )
            for i in data.get("firstScreen") or []
            if isinstance(i, dict)
        ],
    )


def parse_broken_images(data: dict[str, Any]) -> list[BrokenImage]:
    return [
        BrokenImage(str(i.get("src", "")), bool(i.get("hero")))
        for i in data.get("brokenImages") or []
        if isinstance(i, dict)
    ]


def parse_junk(data: dict[str, Any]) -> list[JunkText]:
    return [
        JunkText(str(i.get("token", "")), str(i.get("snippet", ""))[:80])
        for i in data.get("junk") or []
        if isinstance(i, dict)
    ]


def parse_storage(items: Any) -> list[StorageEntry]:
    out = []
    for i in items or []:
        if not isinstance(i, dict):
            continue
        exp = i.get("expDays")
        out.append(
            StorageEntry(
                area=str(i.get("area", "")),
                key=str(i.get("key", ""))[:100],
                length=int(i.get("length") or 0),
                jwt=bool(i.get("jwt")),
                jwt_exp_days=float(exp) if isinstance(exp, int | float) else None,
            )
        )
    return out
