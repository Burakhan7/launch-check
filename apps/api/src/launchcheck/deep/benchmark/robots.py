"""Rakip ve referans sitelerde robots.txt'e uyum (PHASE2 §7.5, §12.4).

RFC 9309: robots.txt yoksa (4xx) her şeye izin var; sunucu hatası (5xx) veya erişilemezlik
"hepsi yasak" sayılır. Kullanıcı ajanı belirteci `LaunchCheckBot`. İstek SSRF korumalı.
"""

from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

from launchcheck.collectors.fetcher import FetchError, SafeFetcher

BOT_TOKEN = "LaunchCheckBot"  # noqa: S105 (robots.txt kullanıcı ajanı adı, gizli değil)
MAX_ROBOTS_BYTES = 512 * 1024


@dataclass
class RobotsPolicy:
    origin: str
    reachable: bool
    _parser: RobotFileParser | None = None
    _allow_all: bool = False

    def allowed(self, url: str) -> bool:
        if not self.reachable:
            return False
        if self._allow_all:
            return True
        return self._parser is not None and self._parser.can_fetch(BOT_TOKEN, url)


def parse_policy(origin: str, status: int | None, body: str) -> RobotsPolicy:
    if status is None or status >= 500:
        return RobotsPolicy(origin, reachable=False)
    if 400 <= status < 500:
        return RobotsPolicy(origin, reachable=True, _allow_all=True)
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    return RobotsPolicy(origin, reachable=True, _parser=parser)


async def fetch_policy(fetcher: SafeFetcher, origin: str) -> RobotsPolicy:
    try:
        res = await fetcher.fetch(origin.rstrip("/") + "/robots.txt", MAX_ROBOTS_BYTES)
    except FetchError:
        return parse_policy(origin, None, "")
    return parse_policy(origin, res.status, res.text)
