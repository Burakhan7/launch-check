"""retire.js veritabanının haftalık güncellenmesi (worker-deep cron).

İndirme SSRF korumalı `SafeFetcher` ile yapılır; yanıt biçimi doğrulanmadan (ayrıştırılıp en az
`MIN_LIBRARIES` kütüphane içermeden) mevcut kopyanın yerine yazılmaz. Yazım atomiktir
(geçici dosya + `os.replace`). Başarısız olursa imajdaki kopya kullanılmaya devam eder.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

from launchcheck.collectors.fetcher import FetchError, SafeFetcher
from launchcheck.config import Settings
from launchcheck.deep.supply.analysis import RETIRE_FILE
from launchcheck.deep.supply.retire import RetireDb
from launchcheck.security.ssrf import UrlGuard

logger = logging.getLogger(__name__)
MIN_LIBRARIES = 30


async def update_retire_db(settings: Settings, fetcher: SafeFetcher | None = None) -> bool:
    own = fetcher is None
    fetcher = fetcher or SafeFetcher(settings, UrlGuard(timeout_s=settings.dns_timeout_s))
    try:
        res = await fetcher.fetch(settings.deep_retire_update_url, settings.deep_retire_max_bytes)
    except FetchError as exc:
        logger.info("retire güncellemesi indirilemedi (%s)", exc.code)
        return False
    finally:
        if own:
            await fetcher.aclose()
    if res.status != 200 or res.truncated:
        logger.info("retire güncellemesi kullanılamadı (%s)", res.status)
        return False
    try:
        db = RetireDb.parse(json.loads(res.body))
    except ValueError:
        logger.info("retire güncellemesi geçersiz")
        return False
    if len(db.libraries) < MIN_LIBRARIES:
        logger.info("retire güncellemesi eksik görünüyor (%d kütüphane)", len(db.libraries))
        return False
    await asyncio.to_thread(_write_atomic, Path(settings.deep_retire_update_dir), res.body)
    logger.info("retire veritabanı güncellendi (%d kütüphane)", len(db.libraries))
    return True


def _write_atomic(target_dir: Path, body: bytes) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.replace(tmp, target_dir / RETIRE_FILE)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
