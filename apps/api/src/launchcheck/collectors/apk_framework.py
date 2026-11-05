"""APK framework tespiti (meta.framework). Saf fonksiyonlar; zip girdi adları ve smali paketleri."""

import re
from collections.abc import Iterable
from enum import StrEnum

HERMES_MAGIC = bytes.fromhex("c61fbc03c103191f")
RN_BUNDLE = "assets/index.android.bundle"
_FLUTTER_LIB_RE = re.compile(r"^lib/[^/]+/libflutter\.so$")
_RN_LIB_RE = re.compile(r"^lib/[^/]+/lib(reactnativejni|hermes|jsc|reactnative)\.so$")
FLUTTERFLOW_MARKER = "flutter_flow/"


class Framework(StrEnum):
    FLUTTERFLOW = "flutterflow"
    FLUTTER = "flutter"
    EXPO = "expo"
    REACT_NATIVE = "react_native"
    NATIVE = "native"


def detect_framework(
    zip_entries: Iterable[str], smali_packages: set[str], libapp_strings: str
) -> Framework:
    """`smali_packages`: smali klasörlerinde bulunan paket yolları (ör. 'com/facebook/react')."""
    entries = list(zip_entries)
    is_flutter = any(_FLUTTER_LIB_RE.match(e) for e in entries)
    if is_flutter:
        if FLUTTERFLOW_MARKER in libapp_strings or any("flutter_flow" in e for e in entries):
            return Framework.FLUTTERFLOW
        return Framework.FLUTTER
    has_expo = any(p == "expo/modules" or p.startswith("expo/modules/") for p in smali_packages)
    is_rn = (
        RN_BUNDLE in entries
        or any(_RN_LIB_RE.match(e) for e in entries)
        or "com/facebook/react" in smali_packages
    )
    if has_expo:
        return Framework.EXPO
    if is_rn:
        return Framework.REACT_NATIVE
    return Framework.NATIVE


def is_hermes_bundle(head: bytes) -> bool:
    return head.startswith(HERMES_MAGIC)
