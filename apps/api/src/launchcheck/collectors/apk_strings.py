"""APK içinden taranacak string kaynaklarının çıkarımı (saf fonksiyonlar)."""

import re

# Satır numaraları korunur: const-string içermeyen satırlar boş bırakılır.
_CONST_STRING_MARK = "const-string"
_PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{8,}")
_TEXT_PROBE_BYTES = 1024


def smali_const_strings(smali: str) -> str:
    return "\n".join(line if _CONST_STRING_MARK in line else "" for line in smali.splitlines())


def printable_strings(data: bytes) -> str:
    """`strings` benzeri: 8+ karakterlik yazdırılabilir ASCII dizileri, satır satır."""
    return "\n".join(m.group(0).decode("ascii") for m in _PRINTABLE_RE.finditer(data))


def looks_like_text(data: bytes) -> bool:
    return b"\x00" not in data[:_TEXT_PROBE_BYTES]


def decode_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")
