"""APK yardımcılarının araç (apktool/jadx) gerektirmeyen testleri."""

import zipfile
from pathlib import Path

import pytest

from launchcheck.collectors.apk_framework import Framework, detect_framework, is_hermes_bundle
from launchcheck.collectors.apk_signing import (
    SIG_BLOCK_MAGIC,
    extract_common_names,
    find_signing_block,
    iter_block_pairs,
    read_signing_info,
)
from launchcheck.collectors.apk_strings import (
    looks_like_text,
    printable_strings,
    smali_const_strings,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "apk"


@pytest.mark.parametrize(
    ("fixture", "debug"),
    [
        ("native_release", False),
        ("native_debug", True),
        ("flutter_debug", True),
        ("rn_release", False),
        ("expo_dev", True),
    ],
)
def test_v1_certificate_common_name(fixture: str, debug: bool) -> None:
    info = read_signing_info(FIXTURES / f"{fixture}.apk")
    assert info.schemes == ["v1"]
    assert info.is_debug is debug
    assert info.common_names


def cn_der(value: str, tag: int = 0x0C) -> bytes:
    raw = value.encode("utf-16-be") if tag == 0x1E else value.encode()
    return b"\x30\x10\x06\x03\x55\x04\x03" + bytes([tag, len(raw)]) + raw


def test_extract_common_names_string_types() -> None:
    der = cn_der("Android Debug") + cn_der("Other", 0x13) + cn_der("Ünicode", 0x1E)
    assert extract_common_names(der) == ["Android Debug", "Other", "Ünicode"]


def build_v2_apk(path: Path, cert_der: bytes) -> None:
    """Merkezi dizinden önce bir APK Signing Block yerleştirilmiş sentetik APK."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("AndroidManifest.xml", b"x")
    data = path.read_bytes()
    eocd = data.rfind(b"PK\x05\x06")
    cd_offset = int.from_bytes(data[eocd + 16 : eocd + 20], "little")
    value = b"\x00" * 8 + cert_der
    pair = (len(value) + 4).to_bytes(8, "little") + (0x7109871A).to_bytes(4, "little") + value
    size = len(pair) + 8 + 16
    block = size.to_bytes(8, "little") + pair + size.to_bytes(8, "little") + SIG_BLOCK_MAGIC
    new_cd = cd_offset + len(block)
    rest = data[cd_offset:]
    new_eocd = eocd - cd_offset
    rest = rest[: new_eocd + 16] + new_cd.to_bytes(4, "little") + rest[new_eocd + 20 :]
    path.write_bytes(data[:cd_offset] + block + rest)


def test_v2_signing_block_parsed(tmp_path: Path) -> None:
    apk = tmp_path / "v2.apk"
    build_v2_apk(apk, cn_der("Android Debug"))
    block = find_signing_block(apk.read_bytes())
    assert block is not None
    assert [pid for pid, _ in iter_block_pairs(block)] == [0x7109871A]
    info = read_signing_info(apk)
    assert info.schemes == ["v2"]
    assert info.is_debug
    assert zipfile.ZipFile(apk).namelist() == ["AndroidManifest.xml"]  # zip hâlâ geçerli


def test_unsigned_apk(tmp_path: Path) -> None:
    apk = tmp_path / "u.apk"
    with zipfile.ZipFile(apk, "w") as zf:
        zf.writestr("AndroidManifest.xml", b"x")
    info = read_signing_info(apk)
    assert (info.schemes, info.common_names, info.is_debug) == ([], [], False)


@pytest.mark.parametrize(
    ("entries", "packages", "libapp", "expected"),
    [
        (["lib/arm64-v8a/libflutter.so"], set(), "", Framework.FLUTTER),
        (
            ["lib/arm64-v8a/libflutter.so"],
            set(),
            "package:a/flutter_flow/x.dart",
            Framework.FLUTTERFLOW,
        ),
        (["assets/index.android.bundle"], set(), "", Framework.REACT_NATIVE),
        (["lib/arm64-v8a/libhermes.so"], set(), "", Framework.REACT_NATIVE),
        ([], {"com/facebook/react"}, "", Framework.REACT_NATIVE),
        ([], {"com/facebook/react", "expo/modules/core"}, "", Framework.EXPO),
        (["classes.dex"], {"com/example/app"}, "", Framework.NATIVE),
    ],
)
def test_detect_framework(
    entries: list[str], packages: set[str], libapp: str, expected: Framework
) -> None:
    assert detect_framework(entries, packages, libapp) == expected


def test_strings_helpers() -> None:
    smali = '.method a()V\n    const-string v0, "secret-here"\n    return-void\n'
    out = smali_const_strings(smali)
    assert out.splitlines()[1].endswith('"secret-here"')
    assert out.count("\n") == smali.count("\n") - 1  # satır numaraları korunur
    assert printable_strings(b"\x00\x01short\x00a-long-string-here\x02") == "a-long-string-here"
    assert looks_like_text(b"KEY=value\n")
    assert not looks_like_text(b"\x7fELF\x00\x00")
    assert is_hermes_bundle(bytes.fromhex("c61fbc03c103191f") + b"rest")
    assert not is_hermes_bundle(b"var __BUNDLE_START_TIME__")
