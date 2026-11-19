"""Faz 2 check tabanı: `DeepContext` üzerinde çalışır, modül verisi yoksa bulgu üretmez."""

from abc import abstractmethod
from typing import ClassVar
from urllib.parse import urlsplit

from launchcheck.checks.base import Check, Finding, ScanContext, TargetType
from launchcheck.deep.context import DeepContext
from launchcheck.deep.render_data import RenderData, RenderedPage

MAX_ITEMS = 10
MAX_PAGES = 5


class RenderCheck(Check):
    target = TargetType.WEB
    phase = 2
    module: ClassVar[str | None] = "render"

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, DeepContext):
            raise TypeError("DeepContext bekleniyordu")
        if ctx.render is None or not ctx.render.pages:
            return []
        return await self.check(ctx, ctx.render)

    @abstractmethod
    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]: ...


def path_of(url: str) -> str:
    parts = urlsplit(url)
    return parts.path or "/"


def page_list(pages: list[RenderedPage]) -> list[str]:
    return list(dict.fromkeys(path_of(p.url) for p in pages))[:MAX_PAGES]
