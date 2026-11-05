from launchcheck.collectors.platform import backend_hints, detect_platforms
from launchcheck.collectors.web_assets import extract_chunk_refs, extract_html_scripts

ORIGIN = "https://app.example.com"

HTML = """<!doctype html><html><head>
<script type="module" crossorigin src="/assets/index-AbC123.js"></script>
<link rel="modulepreload" href="/assets/vendor-x1.js">
<link rel="preload" as="script" href="./assets/extra.js">
<link rel="preload" as="style" href="/assets/style.css">
<script src="https://cdn.other.com/lib.js"></script>
<script src="/assets/index-AbC123.js"></script>
<script>window.__CFG__ = {k: 1}</script>
<script>   </script>
</head><body></body></html>"""


def test_extract_html_scripts() -> None:
    page = extract_html_scripts(HTML, ORIGIN + "/")
    assert page.external == [
        f"{ORIGIN}/assets/index-AbC123.js",
        f"{ORIGIN}/assets/vendor-x1.js",
        f"{ORIGIN}/assets/extra.js",
    ]
    assert "https://cdn.other.com/lib.js" in page.all_external
    assert page.inline == ["window.__CFG__ = {k: 1}"]


def test_extract_chunk_refs_vite_and_next() -> None:
    js = """
      const a = () => import("./Dashboard-9f8e.js");
      __vitePreload(() => import("./Settings-1a.js"), ["assets/Settings-1a.js","assets/x.css"]);
      self.__BUILD_MANIFEST = {"/": ["static/chunks/pages/index-abc.js"]};
      fetch("/api/data.js"); const lib = "react.js"; const ext = "https://cdn.x.com/a/b.js";
    """
    refs = extract_chunk_refs(js, f"{ORIGIN}/assets/index-AbC123.js", ORIGIN)
    assert f"{ORIGIN}/assets/Dashboard-9f8e.js" in refs
    assert f"{ORIGIN}/assets/Settings-1a.js" in refs
    assert f"{ORIGIN}/_next/static/chunks/pages/index-abc.js" in refs
    assert f"{ORIGIN}/api/data.js" in refs
    assert not any("react.js" in r for r in refs)  # "/" içermeyen string elenir
    assert not any("cdn.x.com" in r for r in refs)  # farklı origin
    assert len(refs) == len(set(refs))


def test_detect_platforms() -> None:
    lovable_html = '<meta name="author" content="Lovable" /><script src="/assets/index-a1.js">'
    platforms = detect_platforms(
        "https://my-app.lovable.app/",
        {"server": "cloudflare"},
        lovable_html,
        ["https://my-app.lovable.app/assets/index-a1.js"],
        ['createClient("https://abcdefghijklmnopqrst.supabase.co", "anon")'],
    )
    assert platforms == ["lovable", "supabase", "vite"]


def test_detect_platforms_next_vercel_firebase() -> None:
    platforms = detect_platforms(
        "https://shop.example.com/",
        {"x-vercel-id": "fra1::abc", "x-powered-by": "Next.js"},
        '<script id="__NEXT_DATA__" type="application/json">{}</script>',
        [],
        ['initializeApp({apiKey:"AIza...",authDomain:"p.firebaseapp.com",projectId:"p-1"})'],
    )
    assert platforms == ["firebase", "nextjs", "vercel"]


def test_detect_nothing() -> None:
    assert detect_platforms("https://example.com/", {}, "<html></html>", [], []) == []


def test_backend_hints() -> None:
    hints = backend_hints(
        [
            "https://abcdefghijklmnopqrst.supabase.co/rest/v1",
            'projectId: "my-proj-42"',
            "https://abcdefghijklmnopqrst.supabase.co",
        ]
    )
    assert hints == {
        "supabase_urls": ["https://abcdefghijklmnopqrst.supabase.co"],
        "firebase_project_ids": ["my-proj-42"],
    }
