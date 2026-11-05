"""Check kaydı. Check'ler `@register` ile otomatik toplanır."""

import asyncio
import importlib
import logging
import pkgutil
import re
from dataclasses import dataclass, field

from launchcheck.checks.base import Category, Check, Finding, ScanContext, Severity, TargetType

logger = logging.getLogger(__name__)

CHECK_ID_PATTERN = re.compile(r"^(web|apk)\.[a-z0-9_]+\.[a-z0-9_]+$")
BUILTIN_CHECK_PACKAGES = ("launchcheck.checks.web", "launchcheck.checks.apk")


class Registry:
    def __init__(self) -> None:
        self._checks: dict[str, Check] = {}

    def register[C: type[Check]](self, cls: C) -> C:
        _validate_check_class(cls)
        if cls.id in self._checks:
            raise ValueError(f"check id zaten kayıtlı: {cls.id}")
        self._checks[cls.id] = cls()
        return cls

    def get(self, check_id: str) -> Check | None:
        return self._checks.get(check_id)

    def all(self) -> list[Check]:
        return sorted(self._checks.values(), key=lambda c: c.id)

    def for_target(self, target: TargetType, phase: int) -> list[Check]:
        return [c for c in self.all() if c.target == target and c.phase <= phase]


def _validate_check_class(cls: type[Check]) -> None:
    check_id = getattr(cls, "id", None)
    if not isinstance(check_id, str) or not CHECK_ID_PATTERN.match(check_id):
        raise ValueError(f"geçersiz check id: {check_id!r}")
    target = getattr(cls, "target", None)
    if not isinstance(target, TargetType) or not check_id.startswith(f"{target}."):
        raise ValueError(f"{check_id}: target, id önekiyle uyuşmuyor")
    if not isinstance(getattr(cls, "category", None), Category):
        raise ValueError(f"{check_id}: category eksik")
    if not isinstance(getattr(cls, "default_severity", None), Severity):
        raise ValueError(f"{check_id}: default_severity eksik")
    if cls.phase < 1:
        raise ValueError(f"{check_id}: phase >= 1 olmalı")


registry = Registry()
register = registry.register

_loaded = False


def load_builtin_checks() -> Registry:
    """checks.web ve checks.apk altındaki tüm modülleri import eder (decorator'lar çalışır)."""
    global _loaded
    if not _loaded:
        for package_name in BUILTIN_CHECK_PACKAGES:
            package = importlib.import_module(package_name)
            for module in pkgutil.walk_packages(package.__path__, f"{package_name}."):
                importlib.import_module(module.name)
        _loaded = True
    return registry


@dataclass
class CheckRunResult:
    findings: list[Finding] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def _run_one(check: Check, ctx: ScanContext) -> list[Finding] | None:
    try:
        findings = await check.run(ctx)
    except Exception as exc:
        # İstisna mesajı içerik (HTML, secret) taşıyabilir: sadece tipi logla.
        logger.warning("check başarısız: %s (%s)", check.id, type(exc).__name__)
        return None
    foreign = [f for f in findings if f.check_id != check.id]
    if foreign:
        logger.warning("check başka id ile bulgu üretti, yok sayıldı: %s", check.id)
    return [f for f in findings if f.check_id == check.id]


async def run_checks(ctx: ScanContext, checks: list[Check]) -> CheckRunResult:
    """Check'leri çalıştırır. Bir check hata verirse tarama durmaz, `failed` listesine eklenir."""
    results = await asyncio.gather(*(_run_one(c, ctx) for c in checks))
    out = CheckRunResult()
    for check, findings in zip(checks, results, strict=True):
        if findings is None:
            out.failed.append(check.id)
        else:
            out.findings.extend(findings)
    return out
