"""Collector arayüzü. Web collector M2'de, APK collector M4'te eklenir.

Sözleşme: `collect` başarısız olursa kendi açtığı geçici kaynakları kendisi temizler;
başarılı olursa temizlik `ScanContext.aclose()`'tadır (pipeline her durumda çağırır).
"""

import uuid
from typing import Any, Protocol

from launchcheck.checks.base import ScanContext, TargetType


class CollectorError(Exception):
    """Kullanıcıya gösterilebilir hata. `code` i18n `errors` anahtarıdır."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Collector(Protocol):
    async def collect(self, scan_id: uuid.UUID, args: dict[str, Any]) -> ScanContext: ...


_collectors: dict[TargetType, Collector] = {}


def register_collector(target: TargetType, collector: Collector) -> None:
    _collectors[target] = collector


def get_collector(target: TargetType) -> Collector:
    collector = _collectors.get(target)
    if collector is None:
        raise CollectorError("collector_unavailable")
    return collector
