// Sayfa içi ölçüm (launchcheck/deep/browser/viewport.py). opts: {mobile, full}
(opts) => {
  // Layout viewport: mobil emülasyonda taşan sayfa uzaklaştırılınca innerWidth büyür (görsel
  // viewport); clientWidth etiketin verdiği genişlikte kalır (etiket yoksa 980).
  const vw = document.documentElement.clientWidth || window.innerWidth;
  const vh = document.documentElement.clientHeight || window.innerHeight;
  const CHROME = 'header, nav, footer, aside, [role=navigation], [role=banner], [role=contentinfo]';
  const style = (el) => getComputedStyle(el);
  const visible = (el) => {
    const s = style(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const short = (t) => (t || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const sel = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) return s + '#' + el.id;
    const cls = [...el.classList].filter((c) => c.length < 30).slice(0, 2);
    return cls.length ? s + '.' + cls.join('.') : s;
  };
  const label = (el) => short(el.innerText || el.getAttribute('aria-label') || el.getAttribute('alt')
    || el.getAttribute('title') || el.value || '');
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {tag: el.tagName.toLowerCase(), text: label(el), x: r.left + scrollX, y: r.top + scrollY,
            w: r.width, h: r.height, selector: sel(el)};
  };
  const inChrome = (el) => !!el.closest(CHROME);
  const words = (t) => (t || '').split(/\s+/).filter(Boolean).length;
  const out = {innerWidth: vw, innerHeight: vh};
  const all = [...document.body.querySelectorAll('*')];

  // --- yatay taşma: sayfa genişliği viewport'u aşıyorsa sorumlu en dış öğeler
  const scrollWidth = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
  out.scrollWidth = scrollWidth;
  const clipped = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const o = style(p).overflowX;
      if (o === 'hidden' || o === 'auto' || o === 'scroll' || o === 'clip') return true;
    }
    return false;
  };
  out.overflowing = [];
  if (scrollWidth > vw + 1) {
    for (const el of all) {
      if (out.overflowing.length >= 10) break;
      const r = el.getBoundingClientRect();
      if (r.right <= vw + 1 || !visible(el) || clipped(el)) continue;
      const pr = el.parentElement ? el.parentElement.getBoundingClientRect() : null;
      if (!pr || pr.right <= vw + 1) out.overflowing.push(box(el));
    }
  }

  // --- üst üste binen metin/düğme kutuları (sabit/yapışkan katmanlar hariç)
  const ownText = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
  const interactive = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button]';
  const fixedLayer = (el) => {
    for (let p = el; p && p !== document.body; p = p.parentElement) {
      const pos = style(p).position;
      if (pos === 'fixed' || pos === 'sticky') return true;
    }
    return false;
  };
  const leaves = all.filter((el) => (ownText(el) || el.matches(interactive)) && visible(el)
    && el.getAttribute('aria-hidden') !== 'true' && !fixedLayer(el)).slice(0, 400);
  out.overlaps = [];
  for (let i = 0; i < leaves.length && out.overlaps.length < 10; i++) {
    const a = leaves[i], ra = a.getBoundingClientRect();
    for (let j = i + 1; j < leaves.length; j++) {
      const b = leaves[j];
      if (a.contains(b) || b.contains(a)) continue;
      const rb = b.getBoundingClientRect();
      const ix = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const iy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (ix <= 0 || iy <= 0) continue;
      const smaller = Math.min(ra.width * ra.height, rb.width * rb.height);
      if (ix * iy > 0.5 * smaller) { out.overlaps.push([box(a), box(b)]); break; }
    }
  }

  // --- küçük dokunma hedefleri (sadece mobil; cümle içindeki satır içi linkler hariç)
  out.smallTargets = [];
  out.smallTargetCount = 0;
  if (opts.mobile) {
    for (const el of document.querySelectorAll(interactive)) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width >= 24 && r.height >= 24) continue;
      if (el.tagName === 'A' && style(el).display === 'inline') {
        const parentText = (el.parentElement && el.parentElement.innerText) || '';
        if (words(parentText) > words(el.innerText) + 2) continue;
      }
      out.smallTargetCount += 1;
      if (out.smallTargets.length < 15) out.smallTargets.push(box(el));
    }
  }

  // --- birincil CTA: menü dışı, "btn/cta/primary" sınıflı veya hero içindeki ilk eylem
  const h1 = document.querySelector('h1');
  let hero = null;
  if (h1) {
    hero = h1.parentElement;
    for (let p = h1.parentElement, i = 0; p && i < 2; p = p.parentElement, i++) {
      if (/hero|banner|jumbotron|masthead|intro|splash/i.test(p.className || '')) { hero = p; break; }
    }
    if (hero && ['MAIN', 'ARTICLE', 'BODY', 'HTML'].includes(hero.tagName)) hero = null;
  }
  const ctaCandidates = [...document.querySelectorAll('a[href], button')].filter((el) =>
    !inChrome(el) && visible(el) && label(el) && el.type !== 'submit'
    && (/btn|button|cta|primary/i.test(el.className || '') || (hero && hero.contains(el))));
  const cta = ctaCandidates[0] || null;
  out.primaryCta = cta ? box(cta) : null;

  // --- ilk ekran özeti
  const kinds = [['h1, h2, h3', 'heading'], ['p', 'paragraph'], ['button, a[href]', 'link'],
                 ['img', 'image'], ['video, iframe[src*="youtube"], iframe[src*="vimeo"]', 'video'],
                 ['form', 'form']];
  const first = [];
  for (const [selector, kind] of kinds) {
    for (const el of document.querySelectorAll(selector)) {
      const r = el.getBoundingClientRect();
      if (r.top >= vh || r.bottom <= 0 || !visible(el)) continue;
      if (kind === 'paragraph' && words(el.innerText) < 4) continue;
      if (kind === 'image' && r.width * r.height < vw * vh * 0.02) continue;
      if (kind === 'link' && inChrome(el)) continue;
      const cy = r.top + r.height / 2;
      const visibleArea = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0)) * Math.min(r.width, vw);
      first.push({kind: kind === 'link' && /btn|button|cta/i.test(el.className || '') ? 'button' : kind,
                  text: label(el), top: r.top, position: cy < vh / 3 ? 'top' : cy < 2 * vh / 3 ? 'middle' : 'bottom',
                  size_pct: Math.round(1000 * visibleArea / (vw * vh)) / 10, primary_cta: el === cta});
    }
  }
  // logo ızgarası: en az 4 küçük görsel içeren kapsayıcı
  for (const el of document.querySelectorAll('ul, div, section')) {
    const imgs = el.querySelectorAll(':scope > * img, :scope > img');
    const r = el.getBoundingClientRect();
    if (imgs.length >= 4 && r.top < vh && r.bottom > 0 && r.height < vh * 0.4 && visible(el)) {
      first.push({kind: 'logo_grid', text: '', top: r.top, position: 'middle', size_pct: 0, primary_cta: false});
      break;
    }
  }
  out.firstScreen = first.sort((a, b) => a.top - b.top).slice(0, 25);

  // Bot doğrulama sayfası (Cloudflare, Vercel Security Checkpoint, DataDome...): taranmaz
  out.challenge = /just a moment|attention required|security checkpoint|verify you are human/i
    .test(document.title || '')
    || !!document.querySelector('#challenge-form, [id^=cf-chl], script[src*="challenge-platform"], '
      + 'script[src*="captcha-delivery.com"]');

  if (!opts.full) return out;

  // --- sayfa bilgileri (tek görünümde yeterli)
  const meta = (p) => { const m = document.querySelector(`meta[property="${p}"], meta[name="${p}"]`); return m ? (m.content || '') : ''; };
  out.title = short(document.title);
  out.h1 = h1 ? short(h1.innerText) : '';
  const canon = document.querySelector('link[rel=canonical]');
  out.canonical = canon ? canon.href : '';
  out.og = {};
  for (const p of ['og:title', 'og:description', 'og:image']) { const v = meta(p); if (v) out.og[p] = v.slice(0, 300); }
  const chromeWords = [...document.querySelectorAll(CHROME)].filter((el) => !el.parentElement.closest(CHROME))
    .reduce((n, el) => n + words(el.innerText), 0);
  out.mainWords = Math.max(0, words(document.body.innerText) - chromeWords);
  const links = new Set(), priority = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    let u;
    try { u = new URL(a.getAttribute('href'), location.href); } catch (e) { continue; }
    if (u.origin !== location.origin || !/^https?:$/.test(u.protocol)) continue;
    const clean = u.origin + u.pathname;
    links.add(clean);
    if (inChrome(a)) priority.add(clean);
  }
  out.links = [...links].slice(0, 200);
  out.priorityLinks = [...priority].slice(0, 100);
  out.brokenImages = [];
  for (const img of document.querySelectorAll('img')) {
    const src = img.currentSrc || img.getAttribute('src') || '';
    if (!src || src.startsWith('data:') || !img.complete) continue;
    const r = img.getBoundingClientRect();
    if (img.naturalWidth === 0 && style(img).display !== 'none') {
      out.brokenImages.push({src: src.split('?')[0].slice(0, 300), hero: r.top < vh && r.width > vw * 0.3});
      if (out.brokenImages.length >= 15) break;
    }
  }
  out.junk = [];
  const JUNK = /(^|[^\w])(undefined|NaN|null)([^\w]|$)|\[object Object\]|\{\{[^}]{0,40}\}\}|Invalid Date/;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n && out.junk.length < 10; n = walker.nextNode()) {
    const t = n.textContent.replace(/\s+/g, ' ').trim();
    if (!t || t.length > 60) continue;
    const parent = n.parentElement;
    if (!parent || parent.closest('script, style, code, pre, textarea, noscript, template, kbd, samp')) continue;
    if (!visible(parent)) continue;
    const m = t.match(JUNK);
    if (m) out.junk.push({token: (m[2] || m[0]).trim(), snippet: t});
  }
  out.hasPassword = !!document.querySelector('input[type=password]');
  const headings = [...document.querySelectorAll('h1, h2, title')].map((e) => e.innerText || e.textContent || '').join(' ');
  out.admin = [];
  if (/\b(admin|administrator|y[öo]netim|y[öo]netici|dashboard|kontrol paneli|y[öo]netim paneli|backoffice)\b/i.test(headings)) out.admin.push('admin_heading');
  if ([...document.querySelectorAll('table')].some((t) => t.querySelectorAll('tbody tr, tr').length >= 4)) out.admin.push('data_table');
  const destructive = [...document.querySelectorAll('button, a[href], [role=button]')]
    .filter((el) => /^(sil|düzenle|duzenle|delete|edit|remove|ban|engelle|kaldır|kaldir)$/i.test(label(el))).length;
  if (destructive >= 2) out.admin.push('destructive_buttons');
  return out;
}
