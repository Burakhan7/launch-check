"""Örnek site anatomisi ve ekran görüntüsü önbelleği (PHASE2 §7.5).

- Rakipler: 24 saat (`LC_BENCHMARK_CACHE_TTL_H`), tüm kullanıcılar arasında ortak. Anatomi Redis'te
  (anahtar: normalize origin), ekran görüntüleri asset volume'unda `_benchmark/<hash>/` altında.
- Referans siteler: haftalık taranır; ekran görüntüleri `_benchmark/reference/<hash>/` altında,
  anatomi DB'de.
Rapora girerken ekran görüntüsü taramanın kendi asset'i olarak kopyalanır (7 gün sonra silinir).
`_` ile başlayan klasörler yetim asset süpürücüsünün dışındadır; temizliği `sweep()` yapar.
"""

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from launchcheck.deep.assets import is_webp

logger = logging.getLogger(__name__)
CACHE_DIR = "_benchmark"
REFERENCE_DIR = "reference"
KEY_PREFIX = "bench:site:"


class RedisJson(Protocol):
    async def get(self, key: str) -> bytes | str | None: ...
    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...


def origin_hash(origin: str) -> str:
    return hashlib.sha256(origin.lower().rstrip("/").encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Shot:
    path: str  # assets_dir'e göre göreli
    width: int
    height: int


class ShotStore:
    def __init__(self, assets_dir: Path, max_bytes: int) -> None:
        self.base = assets_dir / CACHE_DIR
        self.max_bytes = max_bytes

    def _dir(self, origin: str, reference: bool) -> Path:
        sub = Path(REFERENCE_DIR) / origin_hash(origin) if reference else Path(origin_hash(origin))
        return self.base / sub

    def save(
        self, origin: str, kind: str, data: bytes, width: int, height: int, reference: bool = False
    ) -> Shot | None:
        if not is_webp(data) or len(data) > self.max_bytes or kind not in ("mobile", "desktop"):
            return None
        folder = self._dir(origin, reference)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{kind}.webp"
        path.write_bytes(data)
        return Shot(str(path.relative_to(self.base.parent)).replace("\\", "/"), width, height)

    def read(self, shot: dict[str, Any] | None) -> tuple[bytes, int, int] | None:
        if not isinstance(shot, dict) or not shot.get("path"):
            return None
        path = (self.base.parent / str(shot["path"])).resolve()
        if self.base.resolve() not in path.parents or not path.is_file():
            return None  # klasör dışına çıkan yol veya silinmiş dosya
        data = path.read_bytes()
        return (
            (data, int(shot.get("width") or 0), int(shot.get("height") or 0))
            if is_webp(data)
            else None
        )

    def sweep(self, max_age_s: float, reference_max_age_s: float) -> int:
        """Süresi dolmuş rakip görüntüleri (24 saat) ve güncellenmeyen referans görüntüleri."""
        if not self.base.is_dir():
            return 0
        now = time.time()
        deleted = 0
        for folder in self.base.iterdir():
            if folder.name == REFERENCE_DIR:
                for ref in folder.iterdir():
                    if now - ref.stat().st_mtime > reference_max_age_s:
                        shutil.rmtree(ref, ignore_errors=True)
                        deleted += 1
                continue
            if now - folder.stat().st_mtime > max_age_s:
                shutil.rmtree(folder, ignore_errors=True)
                deleted += 1
        return deleted


class AnatomyCache:
    def __init__(self, redis: RedisJson | None, ttl_s: int) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    async def get(self, origin: str) -> dict[str, Any] | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(KEY_PREFIX + origin_hash(origin))
        except Exception as exc:
            logger.info("kıyas önbelleği okunamadı (%s)", type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    async def put(self, origin: str, value: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                KEY_PREFIX + origin_hash(origin), json.dumps(value), ex=self._ttl_s
            )
        except Exception as exc:
            logger.info("kıyas önbelleği yazılamadı (%s)", type(exc).__name__)
