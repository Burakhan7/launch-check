"""Link yoklama: önce HEAD, sunucu HEAD'i desteklemiyorsa küçük gövdeli GET.

Pasiflik: sadece normal bir ziyaretçinin linke tıklamasına eşdeğer istekler (GET/HEAD), sorgu
dizesi olmadan. İstekler SSRF korumalı `SafeFetcher` ile gider. Aynı host'a iki istek arasında
en az `interval_s` beklenir (hedefin kendisi için daha kısa aralık).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from launchcheck.collectors.fetcher import FetchError, FetchResult
from launchcheck.deep.links_data import LinkOutcome

GET_FALLBACK_BYTES = 16 * 1024
# HEAD'i desteklemeyen / farklı yanıtlayan sunucular: GET ile tekrar denenir
HEAD_RETRY_STATUSES = frozenset({400, 403, 404, 405, 406, 409, 429, 500, 501, 502, 503})
BROKEN_STATUSES = frozenset({404, 410})
# Giriş isteyen, botu engelleyen veya hız sınırı koyan yanıtlar: link kırık değil, bilinmiyor
UNKNOWN_STATUSES = frozenset({401, 403, 407, 429, 451, 999})
CHALLENGE_HEADERS = ("cf-mitigated", "x-vercel-mitigated")

Fetch = Callable[[str, int, str], Awaitable[FetchResult]]


class HostPacer:
    """Host başına istek aralığı. Bekleme yapay değildir: karşı sunucuya nezaket sınırıdır."""

    def __init__(self, interval_s: float, own_host: str = "", own_interval_s: float = 0) -> None:
        self._interval_s = interval_s
        self._own_host = own_host
        self._own_interval_s = own_interval_s
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        interval = self._own_interval_s if host == self._own_host else self._interval_s
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host)
            if last is not None:
                delay = last + interval - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


@dataclass(frozen=True)
class ProbeResult:
    outcome: str
    status: int | None
    reason: str


def classify_status(status: int, headers: dict[str, str]) -> ProbeResult:
    if any(headers.get(h, "").lower() == "challenge" for h in CHALLENGE_HEADERS):
        return ProbeResult(LinkOutcome.UNKNOWN, status, "bot_challenge")
    if status in BROKEN_STATUSES:
        return ProbeResult(LinkOutcome.BROKEN, status, f"http_{status}")
    if status in UNKNOWN_STATUSES:
        return ProbeResult(LinkOutcome.UNKNOWN, status, f"http_{status}")
    if status >= 500:
        return ProbeResult(LinkOutcome.BROKEN, status, "http_5xx")
    if status >= 400:
        return ProbeResult(LinkOutcome.UNKNOWN, status, f"http_{status}")
    return ProbeResult(LinkOutcome.OK, status, "")


def classify_error(exc: FetchError) -> ProbeResult:
    """Alan adı hiç çözülmüyorsa veya bağlantı reddediliyorsa link kırıktır. Zaman aşımı ve
    bizim SSRF reddimiz (iç adres) karar vermeye yetmez."""
    if exc.code == "url_unresolvable":
        return ProbeResult(LinkOutcome.BROKEN, None, "dns")
    if exc.code == "url_not_public":
        return ProbeResult(LinkOutcome.UNKNOWN, None, "blocked")
    if exc.code == "too_many_redirects":
        return ProbeResult(LinkOutcome.BROKEN, None, "redirect_loop")
    if exc.detail in ("connect", "tls"):
        return ProbeResult(LinkOutcome.BROKEN, None, exc.detail)
    return ProbeResult(LinkOutcome.UNKNOWN, None, exc.detail or exc.code)


async def probe_link(fetch: Fetch, pacer: HostPacer, url: str) -> ProbeResult:
    host = (urlsplit(url).hostname or "").lower()
    await pacer.wait(host)
    try:
        res = await fetch(url, 0, "HEAD")
    except FetchError as exc:
        result = classify_error(exc)
        if result.outcome == LinkOutcome.UNKNOWN and exc.detail != "timeout":
            return result
        if result.outcome == LinkOutcome.BROKEN and result.reason == "dns":
            return result  # GET de çözülemez
        res = None
    if res is not None and res.status not in HEAD_RETRY_STATUSES:
        return classify_status(res.status, res.headers)
    await pacer.wait(host)
    try:
        res = await fetch(url, GET_FALLBACK_BYTES, "GET")
    except FetchError as exc:
        return classify_error(exc)
    return classify_status(res.status, res.headers)
