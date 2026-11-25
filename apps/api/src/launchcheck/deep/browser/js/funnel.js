// Huni yürüyüşü ölçümleri (launchcheck/deep/browser/funnel.py). Sadece okur: alanların
// DEĞERLERİ okunmaz, hiçbir alana yazılmaz, form gönderilmez. Kararlar Python'da verilir.
// opts: {mode: 'cta' | 'inspect', skipLabels: [regex kaynağı]}
(opts) => {
  const fold = (s) => (s || '').replace(/[ıİ]/g, 'i').replace(/[şŞ]/g, 's').replace(/[ğĞ]/g, 'g')
    .replace(/[üÜ]/g, 'u').replace(/[öÖ]/g, 'o').replace(/[çÇ]/g, 'c').replace(/[âÂ]/g, 'a')
    .replace(/[îÎ]/g, 'i').replace(/[ûÛ]/g, 'u').toLowerCase().replace(/\s+/g, ' ').trim();
  const short = (t, n = 80) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const CHROME = 'header, nav, footer, aside, [role=navigation], [role=banner], [role=contentinfo]';
  const label = (el) => short(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '');
  const nav = performance.getEntriesByType('navigation')[0];
  const timing = nav && nav.loadEventEnd > 0 ? Math.round(nav.loadEventEnd - nav.startTime) : null;

  if (opts.mode === 'cta') {
    // Birincil CTA: menü dışı, form gönderme düğmesi olmayan, "btn/cta/primary" sınıflı veya
    // hero içindeki ilk görünür eylem. Atlanacak etiketler (sepete ekle vb.) dışarıdan gelir.
    const skip = (opts.skipLabels || []).map((s) => new RegExp(s));
    const h1 = document.querySelector('h1');
    let hero = h1 ? h1.parentElement : null;
    for (let p = hero, i = 0; p && i < 3; p = p.parentElement, i++) {
      if (/hero|banner|jumbotron|masthead|intro|splash/i.test(typeof p.className === 'string' ? p.className : '')) { hero = p; break; }
    }
    if (hero && ['MAIN', 'BODY', 'HTML'].includes(hero.tagName)) hero = null;
    const candidates = [...document.querySelectorAll('a[href], button, [role=button]')].filter((el) => {
      if (el.closest(CHROME) || !visible(el) || !label(el)) return false;
      if (el.type === 'submit' || el.closest('form')) return false;
      const text = fold(label(el));
      if (skip.some((r) => r.test(text))) return false;
      return /btn|button|cta|primary/i.test(typeof el.className === 'string' ? el.className : '') || (hero && hero.contains(el));
    });
    const cta = candidates[0] || null;
    if (!cta) return {cta: null, timing};
    cta.setAttribute('data-lc-cta', '1');
    return {cta: {text: label(cta), tag: cta.tagName.toLowerCase(),
                  href: cta.tagName === 'A' ? cta.href.slice(0, 300) : ''}, timing};
  }

  // --- inspect: formlar, sosyal giriş düğmeleri, fiyat bağlamları
  const fieldInfo = (el, root) => {
    let text = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    if (el.id) { const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) text = l.innerText + ' ' + text; }
    const wrap = el.closest('label'); if (wrap) text = wrap.innerText + ' ' + text;
    return {
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || (el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : 'text')).toLowerCase(),
      inputmode: (el.getAttribute('inputmode') || '').toLowerCase(),
      autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
      name: short((el.getAttribute('name') || '') + ' ' + (el.id || ''), 60),
      label: short(text, 80),
      required: el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(short(text)),
    };
  };
  const FIELDS = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image]), select, textarea';
  const groups = [];
  const seen = new Set();
  const addGroup = (root, kind) => {
    const fields = [...root.querySelectorAll(FIELDS)].filter((f) => visible(f) || f.type === 'checkbox');
    if (!fields.length) return;
    fields.forEach((f) => seen.add(f));
    const buttons = [...root.querySelectorAll('button, input[type=submit], [role=button]')].filter(visible).map(label).filter(Boolean).slice(0, 6);
    const dialog = !!root.closest('dialog, [role=dialog], [aria-modal=true]');
    groups.push({kind, fields: fields.slice(0, 40).map((f) => fieldInfo(f, document)), buttons, dialog,
                 iframes: [...root.querySelectorAll('iframe[src]')].map((f) => f.src.split('?')[0].slice(0, 200)).slice(0, 5)});
  };
  for (const form of document.querySelectorAll('form')) addGroup(form, 'form');
  // <form> kullanmayan SPA formları: şifre alanının en yakın ortak kapsayıcısı
  for (const pw of document.querySelectorAll('input[type=password]')) {
    if (seen.has(pw)) continue;
    let root = pw.parentElement;
    for (let i = 0; root && i < 6 && root.querySelectorAll(FIELDS).length < 2; i++) root = root.parentElement;
    if (root) addGroup(root, 'container');
  }
  const PROVIDERS = /google|apple|facebook|github|microsoft|linkedin|twitter|discord|yandex|gitlab/;
  const social = new Set();
  for (const el of document.querySelectorAll('a[href], button, [role=button]')) {
    if (!visible(el)) continue;
    const img = el.querySelector('img[alt]');
    const text = fold(label(el) + ' ' + (img ? img.alt : '') + ' ' + (el.getAttribute('data-provider') || ''));
    const href = el.tagName === 'A' ? fold(el.getAttribute('href') || '') : '';
    const m = text.match(PROVIDERS) || (/oauth|\/auth\/|accounts\.google|appleid\.apple/.test(href) && href.match(PROVIDERS));
    if (m && (text.length < 60)) social.add(m[0]);
  }
  // Fiyat bağlamları: fiyat içeren en yakın kart/kapsayıcının kısa metni (KDV ifadesi aranır)
  const PRICE = /(?:₺|\bTL\b|\bTRY\b|\$|€|£|\bUSD\b|\bEUR\b)\s?\d|\d[\d.,]*\s?(?:₺|\bTL\b|\bTRY\b|\bUSD\b|\bEUR\b|€|\$|£)/;
  const contexts = [];
  const containers = new Set();
  const currencies = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(), i = 0; n && i < 5000 && contexts.length < 30; n = walker.nextNode(), i++) {
    const t = n.textContent;
    if (!t || t.length > 60 || !PRICE.test(t) || !n.parentElement || n.parentElement.closest('script, style')) continue;
    if (!visible(n.parentElement)) continue;
    for (const c of t.match(/₺|\bTL\b|\bTRY\b|\$|€|£|\bUSD\b|\bEUR\b/g) || []) currencies.add(c === '₺' || c === 'TRY' ? 'TL' : c);
    // Fiyatın kartına kadar çık: üst öğe başka bir fiyat da içeriyorsa (kartlar birleşir) veya
    // metni uzunsa (sayfanın geri kalanı, SSS) dur
    const PRICES = new RegExp(PRICE.source, 'g');
    const count = (el) => ((el.innerText || '').match(PRICES) || []).length;
    let box = n.parentElement;
    for (let j = 0; box.parentElement && j < 3; j++) {
      const up = box.parentElement;
      if (count(up) > count(box) || (up.innerText || '').length > 250) break;
      box = up;
    }
    if (containers.has(box)) continue;
    containers.add(box);
    contexts.push(fold(short(box.innerText, 400)));
  }
  const bodyText = fold(short(document.body ? document.body.innerText : '', 6000));
  // Aynı origin linkler (giriş sayfasından kayıt linkine geçiş için; sorgu dizesiz)
  const links = [];
  for (const a of document.querySelectorAll('a[href]')) {
    try {
      const u = new URL(a.getAttribute('href'), location.href);
      // Sadece "?mode=signup" gibi görünüm parametreleri korunur (token taşıyabilecek diğerleri değil)
      const mode = /^\?(mode|tab|view|action|screen)=[a-z_-]{1,20}$/i.test(u.search) ? u.search : '';
      const clean = u.origin + u.pathname + mode;
      if (u.origin === location.origin && !links.includes(clean)) links.push(clean);
    } catch (e) { /* geçersiz adres */ }
    if (links.length >= 150) break;
  }
  return {timing, links, lang: (document.documentElement.lang || '').toLowerCase().slice(0, 10),
          groups: groups.slice(0, 8), social: [...social], prices: contexts.length,
          currencies: [...currencies], priceContexts: contexts, text: bodyText};
}
