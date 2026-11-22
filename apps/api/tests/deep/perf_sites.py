"""Hız testleri için üç farklı yapıda yerel site (Lighthouse gerçek ağa çıkmaz).

1. heavy_spa: Vite benzeri SPA — içerik JS ile 4 sn sonra geliyor (boş ekran), kullanılmayan
   büyük paket, 1600×1200 görsel 200 px gösteriliyor, boyutsuz görseller, ağır üçüncü taraf
   script (cdn.test), sıkıştırma ve önbellek yok.
2. plain_clean: düz HTML, gzip'li ve uzun önbellekli küçük dosyalar → hız bulgusu yok.
3. wp_like: WordPress benzeri sunucu HTML'i — sıkıştırılmamış tema CSS'i/eklenti JS'i ve
   boyutsuz küçük görseller; içerik hemen görünüyor.

`record_lighthouse.py` bu sitelerin Lighthouse raporlarını fixture olarak kaydeder.
"""

import gzip
import io
from pathlib import Path

from PIL import Image

from tests.deep.browser.servers import Route

FIXTURES = Path(__file__).parent.parent / "fixtures" / "deep"
LONG_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


def js_functions(count: int, used: str = "") -> bytes:
    """Çoğu hiç çağrılmayan fonksiyonlardan oluşan paket (Lighthouse coverage'ı kullanılmayan
    olarak sayar)."""
    parts = [
        f"function unused_{i}(a,b){{var x=a*{i}+b;for(var j=0;j<10;j++){{x+=j*{i}}};"
        f"return 'value-{i}-'+x}}"
        for i in range(count)
    ]
    return ("\n".join(parts) + "\n" + used).encode()


def png(width: int, height: int, noise: bool = True) -> bytes:
    img = (
        Image.effect_noise((width, height), 80).convert("RGB")
        if noise
        else Image.new("RGB", (width, height), (40, 90, 160))
    )
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def gzipped(body: bytes, content_type: str) -> Route:
    return Route(
        gzip.compress(body),
        content_type,
        headers={"Content-Encoding": "gzip", **LONG_CACHE},
    )


def heavy_spa(cdn: str) -> tuple[dict[str, Route], dict[str, Route]]:
    """(site rotaları, cdn rotaları)."""
    render = """
setTimeout(function () {
  document.getElementById('root').innerHTML =
    '<h1>Acme ile işini büyüt</h1><p>Her şey tek panelde. Hemen dene.</p>' +
    '<img src="/hero.png" style="width:200px">' +
    '<img src="/a.png"><img src="/b.png"><img src="/c.png">' +
    '<a href="/pricing">Fiyatlar</a>';
}, 4000);
"""
    widget = js_functions(7000, "var t=Date.now();while(Date.now()-t<300){};")
    index = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Acme</title>
<script type="module" src="/assets/index-a1b2c3.js"></script>
<script src="{cdn}/widget.js" async></script></head>
<body><div id="root"></div></body></html>"""
    site = {
        "/": Route(index.encode()),
        "/pricing": Route(index.encode()),
        "/assets/index-a1b2c3.js": Route(js_functions(3000, render), "text/javascript"),
        "/hero.png": Route(png(1600, 1200), "image/png"),
        "/a.png": Route(png(300, 200, noise=False), "image/png"),
        "/b.png": Route(png(300, 200, noise=False), "image/png"),
        "/c.png": Route(png(300, 200, noise=False), "image/png"),
    }
    return site, {"/widget.js": Route(widget, "text/javascript")}


def plain_clean() -> dict[str, Route]:
    page = (FIXTURES / "plain_clean" / "index.html").read_bytes()
    script = js_functions(5, "document.documentElement.dataset.ready='1';")
    page = page.replace(b"</body>", b'<script src="/site.js" defer></script></body>')
    return {
        "/": gzipped(page, "text/html; charset=utf-8"),
        "/site.js": gzipped(script, "text/javascript"),
    }


def wp_like() -> dict[str, Route]:
    page = (FIXTURES / "wp_like" / "index.html").read_text("utf-8")
    icons = "".join(f'<img src="/wp-content/uploads/icon-{i}.png">' for i in range(4))
    page = page.replace(
        "</article>",
        f"<p>{icons}</p></article><script src='/wp-includes/js/plugins.js'></script>",
    )
    css = "".join(f".rule-{i}{{margin:{i % 7}px;color:#{i % 999:03d}}}\n" for i in range(1500))
    plugins = js_functions(20, "document.body.classList.add('js');" * 2000)
    routes = {
        "/": Route(page.encode()),
        "/wp-content/themes/x/style.css": Route(css.encode(), "text/css", headers=LONG_CACHE),
        "/wp-includes/js/plugins.js": Route(plugins, "text/javascript", headers=LONG_CACHE),
    }
    for i in range(4):
        routes[f"/wp-content/uploads/icon-{i}.png"] = Route(
            png(48, 48, noise=False), "image/png", headers=LONG_CACHE
        )
    return routes
