"""Hassas bilgilerin loglanması (sezgisel, düşük güven).

Sadece uygulamanın KENDİ paketindeki jadx kaynaklarına bakılır; üçüncü taraf kütüphaneler
(Firebase, OkHttp...) "token" kelimesini log mesajlarında sık kullanır ve yanlış alarm üretir.
"""

import re

from launchcheck.checks.apk._common import ApkCheck
from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.dev_urls import MAX_EVIDENCE_ITEMS
from launchcheck.collectors.apk_collector import ApkContext

LOG_CALL_RE = re.compile(
    r"\b(?:Log\.[vdiwe]|System\.out\.print(?:ln)?|System\.err\.print(?:ln)?|Timber\.[vdiwe])\s*\("
)
SENSITIVE_WORD_RE = re.compile(
    r"token|password|passwd|secret|api[_-]?key|authorization|bearer|credential", re.IGNORECASE
)
# Log çağrısından sonra bakılan karakter sayısı (çok satırlı string birleştirmeleri için)
LOOKAHEAD_CHARS = 200
JADX_PREFIX = "sources/"


def _app_files(ctx: ApkContext) -> list[tuple[str, str]]:
    package_dir = ctx.package.replace(".", "/") + "/"
    if package_dir == "/":
        return []
    return [
        (name, text)
        for name, text in ctx.jadx_sources()
        if name.removeprefix(JADX_PREFIX).startswith(package_dir)
    ]


@register
class SensitiveLog(ApkCheck):
    id = "apk.logging.sensitive_log"
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def check(self, ctx: ApkContext) -> list[Finding]:
        locations = []
        for name, text in _app_files(ctx):
            for m in LOG_CALL_RE.finditer(text):
                window = text[m.end() : m.end() + LOOKAHEAD_CHARS].split(";", 1)[0]
                if SENSITIVE_WORD_RE.search(window):
                    locations.append({"file": name, "line": text.count("\n", 0, m.start()) + 1})
        if not locations:
            return []
        return [
            self.finding({"locations": locations[:MAX_EVIDENCE_ITEMS], "count": len(locations)})
        ]
