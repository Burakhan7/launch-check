"""Canlı ilerleme olayları (`scan_events`). Metin değil i18n anahtarı + parametre saklanır; rapor
istendiği dilde render edilir. Parametrelerde URL ve sayılar dışında veri olmamalı."""

import logging
import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.db.models import ScanEvent

logger = logging.getLogger(__name__)

ParamValue = str | int | float | bool


class EventSink(Protocol):
    async def emit(self, module: str, key: str, /, **params: ParamValue) -> None: ...


class DbEventSink:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], scan_id: uuid.UUID) -> None:
        self._sessionmaker = sessionmaker
        self._scan_id = scan_id

    async def emit(self, module: str, key: str, /, **params: ParamValue) -> None:
        # Olay yazılamazsa tarama durmaz (ilerleme akışı yardımcıdır, sonuç değildir).
        try:
            async with self._sessionmaker() as session:
                session.add(ScanEvent(scan_id=self._scan_id, module=module, key=key, params=params))
                await session.commit()
        except Exception as exc:
            logger.warning("olay yazılamadı: %s (%s)", key, type(exc).__name__)


class MemoryEventSink:
    """Testler için."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, ParamValue]]] = []

    async def emit(self, module: str, key: str, /, **params: ParamValue) -> None:
        self.events.append((module, key, params))
