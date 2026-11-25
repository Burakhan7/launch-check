"""Çerez banner'ı tespiti ve "Reddet / Sadece gerekli" tıklaması (PHASE2 §1.3, §6.5).

Pasiflik: sadece banner'daki reddetme düğmesine (bulunamazsa önce "Ayarlar"a, sonra reddetmeye)
tıklanır. Kabul düğmesine, form alanı içeren kapsayıcılara ve banner dışındaki öğelere
dokunulmaz. Banner iframe içindeyse (Sourcepoint, TrustArc) aynı işlem o çerçevede yapılır.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Any

import yaml

from launchcheck.checks.shared import keywords
from launchcheck.deep.browser.viewport import _script
from launchcheck.deep.privacy_data import ConsentInfo, RejectState

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page

logger = logging.getLogger(__name__)

CONSENT_JS = _script("consent.js")
CLICK_TIMEOUT_MS = 3000
SETTINGS_OPEN_WAIT_S = 1.0
MAX_FRAMES = 10
WORD_GROUPS = ("banner", "reject", "settings", "accept")


@dataclass(frozen=True)
class Cmp:
    name: str
    scripts: tuple[str, ...]
    banner: str
    reject: str


@lru_cache
def load_cmps() -> tuple[Cmp, ...]:
    text = resources.files("launchcheck.deep.browser").joinpath("cmps.yaml").read_text("utf-8")
    return tuple(
        Cmp(
            name=str(item["name"]),
            scripts=tuple(str(s) for s in item.get("scripts") or []),
            banner=str(item.get("banner") or ""),
            reject=str(item.get("reject") or ""),
        )
        for item in yaml.safe_load(text) or []
    )


def cmp_from_scripts(urls: list[str]) -> str | None:
    """Banner görünmese bile (ör. daha önce onay verilmiş gibi davranan) yüklenen CMP script'i."""
    for cmp in load_cmps():
        if any(marker in url for url in urls for marker in cmp.scripts):
            return cmp.name
    return None


def js_options(target: str | None) -> dict[str, Any]:
    return {
        "words": {group: keywords.words(f"consent.{group}") for group in WORD_GROUPS},
        "cmps": [{"name": c.name, "banner": c.banner, "reject": c.reject} for c in load_cmps()],
        "target": target,
    }


async def _evaluate(frame: "Frame", target: str | None) -> dict[str, Any] | None:
    try:
        result = await frame.evaluate(CONSENT_JS, js_options(target))
    except Exception as exc:
        logger.info("banner betiği çalışmadı (%s)", type(exc).__name__)
        return None
    return result if isinstance(result, dict) else None


async def detect(page: "Page") -> ConsentInfo:
    info = ConsentInfo()
    for frame in page.frames[:MAX_FRAMES]:
        result = await _evaluate(frame, None)
        if not result:
            continue
        info.cmp = info.cmp or (str(result["cmp"]) if result.get("cmp") else None)
        info.banner = info.banner or bool(result.get("banner"))
    return info


async def _mark_and_click(page: "Page", target: str) -> str | None:
    for frame in page.frames[:MAX_FRAMES]:
        result = await _evaluate(frame, target)
        if not result or not result.get("marked"):
            continue
        try:
            await frame.locator(f'[data-lc-consent="{target}"]').first.click(
                timeout=CLICK_TIMEOUT_MS
            )
        except Exception as exc:
            logger.info("banner düğmesine tıklanamadı (%s)", type(exc).__name__)
            continue
        return str(result.get("label") or "")[:60]
    return None


async def reject(page: "Page", info: ConsentInfo) -> None:
    """Önce ilk katmanda reddet; yoksa "Ayarlar"ı açıp tekrar dene. Sonuç `info`'ya yazılır."""
    if not info.banner:
        info.reject = RejectState.NO_BANNER
        return
    label = await _mark_and_click(page, "reject")
    if label is not None:
        info.reject, info.reject_label = RejectState.CLICKED, label
        return
    if await _mark_and_click(page, "settings") is not None:
        await asyncio.sleep(SETTINGS_OPEN_WAIT_S)  # panel açılma animasyonu (bekleme değil, olay)
        label = await _mark_and_click(page, "reject")
        if label is not None:
            info.reject, info.reject_label = RejectState.CLICKED_SECOND_LAYER, label
            return
    info.reject = RejectState.NOT_FOUND


async def banner_visible(page: "Page") -> bool:
    with contextlib.suppress(Exception):
        return (await detect(page)).banner
    return False
