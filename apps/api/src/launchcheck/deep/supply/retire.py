"""retire.js veritabanıyla (`jsrepository.json`, Apache-2.0) bilinen açığı olan kütüphane tespiti.

Kullanılan çıkarıcılar: `uri` (script adresi), `filename` (dosya adı), `filecontent` (gövde) ve
`hashes` (SHA-1). `func` çıkarıcıları (sayfada JS ifadesi çalıştırır) ve `filecontentreplace`
kullanılmaz: indirilen veritabanındaki ifadeleri tarayıcıda çalıştırmak istemiyoruz.

Hız: ~400 gövde deseni × 60 dosya × birkaç MB doğrudan taranırsa saniyeler sürer. Her desen için
eşleşmede MUTLAKA geçmesi gereken en uzun düz metin parçası çıkarılır; gövdede yoksa desen
çalıştırılmaz. Güvenli olmayan desenlerde (üst düzey `|`) ön filtre kullanılmaz.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLACEHOLDER = "§§version§§"
VERSION_PATTERN = r"[0-9][0-9.a-z_\-]+"
SKIP_KEYS = frozenset({"retire-example", "dont check"})
MIN_LITERAL = 4
SEVERITIES = ("low", "medium", "high", "critical")


def _comparable(part: str | None) -> int | str:
    if not part:
        return 0
    return int(part) if part.isdigit() else part


def is_at_or_above(version: str, other: str) -> bool:
    """retire.js'in karşılaştırması: `.`/`-` ile bölünür, sayılar sayı olarak karşılaştırılır;
    sayı ile metin karşılaştırılırsa sayı büyüktür (3.0.0 > 3.0.0-beta1)."""
    a = re.split(r"[.\-]", version)
    b = re.split(r"[.\-]", other)
    for i in range(max(len(a), len(b))):
        x = _comparable(a[i] if i < len(a) else None)
        y = _comparable(b[i] if i < len(b) else None)
        if type(x) is not type(y):
            return isinstance(x, int)
        if x > y:  # type: ignore[operator]
            return True
        if x < y:  # type: ignore[operator]
            return False
    return True


def required_literal(pattern: str) -> str | None:
    """Desenin her eşleşmesinde geçmek zorunda olan en uzun düz metin parçası. Gruplar,
    karakter sınıfları ve isteğe bağlı karakterler atlanır; üst düzey `|` varsa None."""
    chunks: list[str] = []
    buf: list[str] = []
    i = 0

    def flush() -> None:
        if buf:
            chunks.append("".join(buf))
            buf.clear()

    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            n = pattern[i + 1]
            if n.isalnum():  # \d, \s, \w, \b, \x..
                flush()
            else:
                buf.append(n)
            i += 2
            continue
        if c == "(":
            flush()
            i = _skip_group(pattern, i)
            continue
        if c == "[":
            flush()
            i = _skip_class(pattern, i)
            continue
        if c == "|":
            return None
        if c in "?*":
            if buf:
                buf.pop()
            flush()
        elif c == "{":
            if buf:
                buf.pop()
            flush()
            end = pattern.find("}", i)
            i = end if end != -1 else i
        elif c in ".^$+)":
            flush()
        else:
            buf.append(c)
        i += 1
    flush()
    best = max(chunks, key=len, default="")
    return best if len(best) >= MIN_LITERAL else None


def _skip_group(pattern: str, i: int) -> int:
    depth = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            i = _skip_class(pattern, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _skip_class(pattern: str, i: int) -> int:
    i += 1
    if i < len(pattern) and pattern[i] == "^":
        i += 1
    if i < len(pattern) and pattern[i] == "]":
        i += 1
    while i < len(pattern):
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "]":
            return i + 1
        i += 1
    return i


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    literal: str | None


@dataclass(frozen=True)
class Vulnerability:
    severity: str
    below: str | None
    at_or_above: str | None
    cves: tuple[str, ...]
    summary: str
    info: str
    github_id: str = ""

    def applies(self, version: str) -> bool:
        if self.at_or_above and not is_at_or_above(version, self.at_or_above):
            return False
        return not (self.below and is_at_or_above(version, self.below))


@dataclass
class Library:
    name: str
    uri: list[_Pattern] = field(default_factory=list)
    filename: list[_Pattern] = field(default_factory=list)
    filecontent: list[_Pattern] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    vulnerabilities: list[Vulnerability] = field(default_factory=list)


@dataclass(frozen=True)
class Detection:
    library: str
    version: str
    url: str
    how: str  # uri | filename | filecontent | hash


def _compile(raw: str) -> _Pattern | None:
    source = raw.replace(PLACEHOLDER, VERSION_PATTERN)
    try:
        regex = re.compile(source)
    except re.error:
        return None
    return _Pattern(regex, required_literal(raw.replace(PLACEHOLDER, "")))


def _vuln(item: dict[str, Any]) -> Vulnerability | None:
    severity = str(item.get("severity") or "").lower()
    if severity not in SEVERITIES:
        return None
    ids = item.get("identifiers") or {}
    cves = tuple(str(c) for c in ids.get("CVE") or [] if str(c).startswith("CVE-"))
    info = [str(u) for u in item.get("info") or [] if str(u).startswith("https://")]
    return Vulnerability(
        severity=severity,
        below=str(item["below"]) if item.get("below") else None,
        at_or_above=str(item["atOrAbove"]) if item.get("atOrAbove") else None,
        cves=cves,
        summary=str(ids.get("summary") or "")[:200],
        info=info[0] if info else "",
        github_id=str(ids.get("githubID") or ""),
    )


class RetireDb:
    def __init__(self, libraries: list[Library], version: str = "") -> None:
        self.libraries = libraries
        self.version = version

    @classmethod
    def parse(cls, data: Any) -> "RetireDb":
        if not isinstance(data, dict) or "jquery" not in data:
            raise ValueError("retire veritabanı biçimi tanınmadı")
        libraries = []
        for name, entry in data.items():
            if name in SKIP_KEYS or not isinstance(entry, dict):
                continue
            extractors = entry.get("extractors") or {}
            lib = Library(name=str(name))
            for kind in ("uri", "filename", "filecontent"):
                for raw in extractors.get(kind) or []:
                    pattern = _compile(str(raw))
                    if pattern is not None:
                        getattr(lib, kind).append(pattern)
            lib.hashes = {str(k): str(v) for k, v in (extractors.get("hashes") or {}).items()}
            lib.vulnerabilities = [
                v for v in (_vuln(i) for i in entry.get("vulnerabilities") or []) if v
            ]
            libraries.append(lib)
        return cls(libraries)

    @classmethod
    def load(cls, path: Path) -> "RetireDb | None":
        try:
            return cls.parse(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError) as exc:
            logger.info("retire veritabanı okunamadı (%s)", type(exc).__name__)
            return None

    def detect(self, url: str, body: str | None = None) -> list[Detection]:
        """Bir script'teki kütüphaneler (kütüphane başına ilk bulunan sürüm)."""
        found: dict[str, Detection] = {}
        clean = url.split("#", 1)[0]
        basename = clean.split("?", 1)[0].rsplit("/", 1)[-1]
        digest = hashlib.sha1(body.encode("utf-8", "replace")).hexdigest() if body else ""  # noqa: S324 (retire DB SHA-1 kullanır)
        for lib in self.libraries:
            hit = (
                _first(lib.uri, clean, "uri")
                or _first(lib.filename, basename, "filename")
                or (_first(lib.filecontent, body, "filecontent") if body else None)
            )
            if hit is None and digest and digest in lib.hashes:
                hit = (lib.hashes[digest], "hash")
            if hit is not None and lib.name not in found:
                found[lib.name] = Detection(lib.name, hit[0], clean.split("?", 1)[0], hit[1])
        return list(found.values())

    def vulnerabilities(self, library: str, version: str) -> list[Vulnerability]:
        for lib in self.libraries:
            if lib.name == library:
                return [v for v in lib.vulnerabilities if v.applies(version)]
        return []


def _first(patterns: list[_Pattern], text: str | None, how: str) -> tuple[str, str] | None:
    if not text:
        return None
    for pattern in patterns:
        if pattern.literal is not None and pattern.literal not in text:
            continue
        match = pattern.regex.search(text)
        if match and match.groups() and match.group(1):
            version = clean_version(match.group(1))
            if version:
                return version, how
    return None


VERSION_SUFFIX_RE = re.compile(r"[.\-_](?:min|slim|js|pack|packed|prod|production|dev|umd|esm)$")


def clean_version(raw: str) -> str:
    """Sürüm deseni açgözlüdür: `jquery-1.8.3.min.js` → `1.8.3.min`. Dosya adı ekleri atılır."""
    version = raw.strip(".-_")
    while True:
        stripped = VERSION_SUFFIX_RE.sub("", version)
        if stripped == version:
            return version.strip(".-_")
        version = stripped
