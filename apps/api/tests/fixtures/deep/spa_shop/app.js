// Vite benzeri SPA: içerik JS ile çizilir. Bilerek hatalarla dolu (test fixture'ı).
const routes = { "/admin": "admin" }; // yönetim rotası (koruma sadece istemcide)

function b64url(obj) {
  return btoa(JSON.stringify(obj)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function home() {
  return `
    <nav><a href="/">Acme</a> <a href="/pricing">Fiyatlar</a> <a href="/about">Hakkımızda</a></nav>
    <main>
      <section class="hero">
        <h1>Acme ile siparişini hızla ver</h1>
        <img src="/hero-big.svg" style="display:block;width:100%;height:1200px" alt="">
        <a class="btn" href="/pricing">Hemen başla</a>
      </section>
      <p>Fiyat: ${undefined} ₺</p>
      <img src="/missing.png" width="300" height="200" alt="Ürün">
      <div style="width:600px;background:#eee">Geniş kampanya bandı burada duruyor</div>
      <div style="position:relative;height:40px">
        <span style="position:absolute;left:0;top:0">Kayıt ol şimdi</span>
        <span style="position:absolute;left:12px;top:2px">Kampanyalar burada</span>
      </div>
      <p class="icons">
        <a href="/s1" style="display:inline-block;width:14px;height:14px">a</a>
        <a href="/s2" style="display:inline-block;width:14px;height:14px">b</a>
        <a href="/s3" style="display:inline-block;width:14px;height:14px">c</a>
        <a href="/s4" style="display:inline-block;width:14px;height:14px">d</a>
      </p>
    </main>`;
}

function pricing() {
  return `<nav><a href="/">Acme</a></nav><main><h1>Fiyatlar</h1>
    <p>Başlangıç paketi aylık 199 TL, profesyonel paket aylık 499 TL. Tüm paketlerde
    ücretsiz kargo ve iade garantisi var, istediğin zaman iptal edebilirsin.</p></main>`;
}

async function about(root) {
  root.innerHTML = "";
  const res = await fetch("/api/about");
  root.innerHTML = `<main><h1>Hakkımızda</h1><p>${await res.text()}</p></main>`;
}

function render(path) {
  const root = document.getElementById("root");
  if (path === "/pricing") root.innerHTML = pricing();
  else if (path === "/about") about(root);
  else root.innerHTML = home();
}

document.addEventListener("click", (event) => {
  const a = event.target.closest("a[href]");
  if (!a || !["/pricing", "/about"].includes(a.getAttribute("href"))) return;
  event.preventDefault();
  history.pushState({}, "", a.getAttribute("href"));
  render(a.getAttribute("href"));
});

render(location.pathname);
fetch("/api/items").then((r) => r.json()).catch(() => {});
const exp = Math.floor(Date.now() / 1000) + 60 * 86400;
const jwt = b64url({ alg: "HS256" }) + "." + b64url({ role: "authenticated", exp }) + ".sig";
localStorage.setItem("sb-abc-auth-token", JSON.stringify({ access_token: jwt }));
localStorage.setItem("theme", "dark");
document.cookie = "session_id=abc123; path=/";
setTimeout(() => {
  const data = null;
  data.items.map((x) => x); // yakalanmamış hata
}, 10);
console.log(routes);
