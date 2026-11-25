"""Faz 2 asset'leri (ekran görüntüleri): `assets_dir/<scan_id>/<asset_id>.webp`.

Dosya içeriği loglanmaz. Silme: tarama silinirken (cron) ve DB'de karşılığı olmayan yetim
dosyalar için saatlik süpürücüyle.
"""

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from launchcheck.db.models import ScanAsset

WEBP_MAGIC = (b"RIFF", b"WEBP")


class AssetError(ValueError):
    pass


def is_webp(data: bytes) -> bool:
    return data[:4] == WEBP_MAGIC[0] and data[8:12] == WEBP_MAGIC[1]


@dataclass(frozen=True)
class AssetStore:
    base_dir: Path
    max_bytes: int

    def scan_dir(self, scan_id: uuid.UUID) -> Path:
        return self.base_dir / str(scan_id)

    def save(
        self,
        scan_id: uuid.UUID,
        kind: str,
        page_url: str,
        data: bytes,
        width: int,
        height: int,
    ) -> ScanAsset:
        """WebP görseli diske yazar ve kaydedilecek satırı döner (commit çağıranın işi)."""
        if not is_webp(data):
            raise AssetError("asset_not_webp")
        if len(data) > self.max_bytes:
            raise AssetError("asset_too_large")
        asset_id = uuid.uuid4()
        relative = f"{scan_id}/{asset_id}.webp"
        path = self.base_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return ScanAsset(
            id=asset_id,
            scan_id=scan_id,
            kind=kind,
            page_url=page_url,
            path=relative,
            width=width,
            height=height,
            bytes=len(data),
        )

    def resolve(self, relative: str) -> Path | None:
        """DB'deki göreli yolu güvenle çözer: klasör dışına çıkan yol (`..`) reddedilir."""
        base = self.base_dir.resolve()
        path = (base / relative).resolve()
        if base not in path.parents or not path.is_file():
            return None
        return path

    def delete_scan(self, scan_id: uuid.UUID) -> None:
        shutil.rmtree(self.scan_dir(scan_id), ignore_errors=True)

    def sweep_orphans(self, known: dict[str, set[str]], max_age_s: int) -> int:
        """`known`: scan_id → asset dosya adları. DB'de karşılığı olmayan ve `max_age_s`'den eski
        klasör/dosyaları siler (yazılmakta olan yeni dosyalar korunsun diye yaş sınırı var)."""
        if not self.base_dir.is_dir():
            return 0
        cutoff = time.time() - max_age_s
        deleted = 0
        for scan_dir in self.base_dir.iterdir():
            if scan_dir.name.startswith("_"):
                continue  # paylaşılan önbellekler (ör. _benchmark): kendi temizlikleri var
            try:
                names = known.get(scan_dir.name)
                if names is None:
                    if scan_dir.stat().st_mtime < cutoff:
                        _remove(scan_dir)
                        deleted += 1
                    continue
                for file in scan_dir.iterdir():
                    if file.name not in names and file.stat().st_mtime < cutoff:
                        _remove(file)
                        deleted += 1
            except OSError:
                continue
        return deleted


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
