"""Faz 2 modül tabanı. Her modül bir şeritte (lane) sırayla çalışır; şeritler paraleldir."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from launchcheck.checks.base import Finding
from launchcheck.config import Settings
from launchcheck.db.models import ScanAsset, Verification
from launchcheck.deep.context import DeepContext, ParentFinding


class Lane(StrEnum):
    BROWSER = "browser"
    LIGHTHOUSE = "lighthouse"
    NETWORK = "network"
    LLM = "llm"


def lane_budget_s(lane: Lane, settings: Settings) -> float:
    budgets = {
        Lane.BROWSER: settings.deep_budget_browser_s,
        Lane.LIGHTHOUSE: settings.deep_budget_lighthouse_s,
        Lane.NETWORK: settings.deep_budget_network_s,
        Lane.LLM: settings.deep_budget_llm_s,
    }
    return budgets[lane]


@dataclass
class ModuleOutput:
    """Modül bulgularını çalıştıkça buraya ekler: süre dolarsa eklenmiş olanlar korunur
    ("aşınca elindekiyle biter")."""

    findings: list[Finding] = field(default_factory=list)
    # Diske yazılmış asset'lerin DB satırları (modül bitince bulgularla birlikte kaydedilir)
    assets: list[ScanAsset] = field(default_factory=list)


class DeepModule(ABC):
    name: ClassVar[str]
    lane: ClassVar[Lane]

    def budget_s(self, settings: Settings) -> float:
        return settings.deep_budget_module_s

    @abstractmethod
    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None: ...


# Faz 1 bulgusunu Faz 2 verisiyle doğrulayan kural (bkz. PHASE2.md §8.5). None → kural yok.
VerificationRule = Callable[[DeepContext, ParentFinding], Verification | None]
