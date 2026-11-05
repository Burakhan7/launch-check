"""APK imza sertifikalarından Common Name (CN) çıkarımı. Saf Python, harici araç yok.

`keytool -printcert -jarfile` sadece v1 (JAR) imzasını okur; minSdk >= 24 olan modern
uygulamalar genelde sadece v2/v3 ile imzalanır. Bu yüzden ikisine de bakılır:
- v1: META-INF/*.RSA|*.DSA|*.EC (PKCS#7, sertifika DER içinde)
- v2/v3: Merkezi dizinden hemen önceki "APK Signing Block"
Sertifika ayrıştırmak yerine DER içinde CN (OID 2.5.4.3) değerleri aranır; amaç sadece
"CN=Android Debug" tespitidir.
"""

import mmap
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

SIG_BLOCK_MAGIC = b"APK Sig Block 42"
EOCD_MAGIC = b"PK\x05\x06"
EOCD_MIN_SIZE = 22
EOCD_CD_OFFSET_POS = 16
BLOCK_IDS = {0x7109871A: "v2", 0xF05368C0: "v3", 0x1B93AD61: "v3.1"}
V1_SIGNATURE_RE = re.compile(r"^META-INF/[^/]+\.(RSA|DSA|EC)$", re.IGNORECASE)
# 06 03 55 04 03 = OID commonName; ardından string türü + kısa uzunluk + değer
_CN_RE = re.compile(rb"\x06\x03\x55\x04\x03([\x0c\x13\x14\x16\x1e])([\x01-\x7f])", re.DOTALL)
DEBUG_CN = "Android Debug"


@dataclass
class SigningInfo:
    schemes: list[str] = field(default_factory=list)
    common_names: list[str] = field(default_factory=list)

    @property
    def is_debug(self) -> bool:
        return DEBUG_CN in self.common_names


def extract_common_names(der: bytes) -> list[str]:
    names = []
    for m in _CN_RE.finditer(der):
        tag, length = m.group(1)[0], m.group(2)[0]
        raw = der[m.end() : m.end() + length]
        value = raw.decode("utf-16-be" if tag == 0x1E else "utf-8", errors="replace")
        if value not in names:
            names.append(value)
    return names


def find_signing_block(data: bytes | mmap.mmap) -> bytes | None:
    eocd = data.rfind(EOCD_MAGIC, max(0, len(data) - 65557))
    if eocd < 0 or eocd + EOCD_MIN_SIZE > len(data):
        return None
    cd_offset = int.from_bytes(
        data[eocd + EOCD_CD_OFFSET_POS : eocd + EOCD_CD_OFFSET_POS + 4], "little"
    )
    if cd_offset < 32 or data[cd_offset - 16 : cd_offset] != SIG_BLOCK_MAGIC:
        return None
    size = int.from_bytes(data[cd_offset - 24 : cd_offset - 16], "little")
    start = cd_offset - size - 8
    if start < 0 or size < 24:
        return None
    return bytes(data[start + 8 : cd_offset - 24])


def iter_block_pairs(block: bytes) -> list[tuple[int, bytes]]:
    pairs = []
    i = 0
    while i + 12 <= len(block):
        length = int.from_bytes(block[i : i + 8], "little")
        if length < 4 or i + 8 + length > len(block):
            break
        pair_id = int.from_bytes(block[i + 8 : i + 12], "little")
        pairs.append((pair_id, block[i + 12 : i + 8 + length]))
        i += 8 + length
    return pairs


def read_signing_info(apk_path: Path) -> SigningInfo:
    info = SigningInfo()
    with zipfile.ZipFile(apk_path) as archive:
        for entry in archive.infolist():
            if V1_SIGNATURE_RE.match(entry.filename) and entry.file_size < 1024 * 1024:
                if "v1" not in info.schemes:
                    info.schemes.append("v1")
                for cn in extract_common_names(archive.read(entry)):
                    if cn not in info.common_names:
                        info.common_names.append(cn)
    with apk_path.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
        block = find_signing_block(data)
    for pair_id, value in iter_block_pairs(block or b""):
        scheme = BLOCK_IDS.get(pair_id)
        if scheme is None:
            continue
        if scheme not in info.schemes:
            info.schemes.append(scheme)
        for cn in extract_common_names(value):
            if cn not in info.common_names:
                info.common_names.append(cn)
    return info
