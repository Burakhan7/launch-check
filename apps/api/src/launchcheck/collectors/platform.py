"""Web platform tespiti (meta.platform). Saf fonksiyonlar; sadece işaretlere bakar."""

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

SUPABASE_URL_RE = re.compile(r"https://([a-z0-9]{20})\.supabase\.co")
FIREBASE_PROJECT_RE = re.compile(r"""projectId["']?\s*[:=]\s*["']([a-z0-9-]{4,40})["']""")
MAX_META_ITEMS = 5


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def detect_platforms(
    final_url: str,
    headers: dict[str, str],
    html: str,
    script_urls: Iterable[str],
    js_sources: Iterable[str],
) -> list[str]:
    host = _host(final_url)
    urls = " ".join(script_urls)
    js = "\n".join(js_sources)
    everything = f"{html}\n{urls}\n{js}"
    lower_html = html.lower()
    server = headers.get("server", "").lower()
    powered = headers.get("x-powered-by", "").lower()

    signals = {
        "lovable": host.endswith(".lovable.app")
        or "lovable-tagger" in everything
        or 'content="lovable' in lower_html
        or "cdn.gpteng.co" in everything,
        "bolt": host.endswith(".bolt.host") or "bolt.new" in lower_html,
        "nextjs": "/_next/static/" in everything or "__NEXT_DATA__" in html or "next.js" in powered,
        "vite": "/@vite/client" in html
        or bool(re.search(r"/assets/index-[\w-]+\.js", urls))
        or "__vitePreload" in js,
        "supabase": ".supabase.co" in everything,
        "firebase": "firebaseapp.com" in everything
        or ("apiKey" in js and "projectId" in js and "authDomain" in js),
        "vercel": "x-vercel-id" in headers or server == "vercel" or host.endswith(".vercel.app"),
        "netlify": "x-nf-request-id" in headers
        or server == "netlify"
        or host.endswith(".netlify.app"),
    }
    return sorted(name for name, hit in signals.items() if hit)


def backend_hints(sources: Iterable[str]) -> dict[str, list[str]]:
    """Faz 3 hazırlığı: herkese açık backend adresleri. Faz 1'de bunlara istek atılmaz."""
    text = "\n".join(sources)
    supabase = list(dict.fromkeys(m.group(0) for m in SUPABASE_URL_RE.finditer(text)))
    firebase = list(dict.fromkeys(m.group(1) for m in FIREBASE_PROJECT_RE.finditer(text)))
    hints: dict[str, list[str]] = {}
    if supabase:
        hints["supabase_urls"] = supabase[:MAX_META_ITEMS]
    if firebase:
        hints["firebase_project_ids"] = firebase[:MAX_META_ITEMS]
    return hints
