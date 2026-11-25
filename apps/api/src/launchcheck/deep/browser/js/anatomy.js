// Sayfa anatomisi ham verisi (launchcheck/deep/benchmark/anatomy.py). Kullanıcının sitesinde ve
// örnek sitelerde AYNI betik çalışır. Sadece yapı ve sayılar döner; bölüm sınıflandırması ve
// kelime kararları Python'da (dile özgü listelerle). Form alanı değeri okunmaz.
async () => {
  const fold = (s) => (s || '').replace(/[ıİ]/g, 'i').replace(/[şŞ]/g, 's').replace(/[ğĞ]/g, 'g')
    .replace(/[üÜ]/g, 'u').replace(/[öÖ]/g, 'o').replace(/[çÇ]/g, 'c').replace(/[âÂ]/g, 'a')
    .replace(/[îÎ]/g, 'i').replace(/[ûÛ]/g, 'u').toLowerCase().replace(/\s+/g, ' ').trim();
  const short = (t, n = 80) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const words = (t) => (t || '').split(/\s+/).filter(Boolean).length;
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const cls = (el) => (typeof el.className === 'string' ? el.className : '').toLowerCase();
  const CHROME = 'header, nav, footer, aside, [role=navigation], [role=banner], [role=contentinfo]';
  const vh = document.documentElement.clientHeight || innerHeight;
  const vw = document.documentElement.clientWidth || innerWidth;

  // --- performans (bu tarayıcıda, kısıtlamasız; kullanıcı ve örnekler aynı yöntemle)
  const observe = (type, pick) => new Promise((res) => {
    let value = null;
    try { new PerformanceObserver((l) => { value = pick(value, l.getEntries()); }).observe({type, buffered: true}); }
    catch (e) { /* desteklenmiyor */ }
    setTimeout(() => res(value), 150);
  });
  const lcp = await observe('largest-contentful-paint', (v, e) => (e.length ? e[e.length - 1].startTime : v));
  const cls_ = await observe('layout-shift', (v, e) => (v || 0) + e.filter((x) => !x.hadRecentInput).reduce((n, x) => n + x.value, 0));
  const jsBytes = performance.getEntriesByType('resource').filter((r) => r.initiatorType === 'script')
    .reduce((n, r) => n + (r.transferSize || r.encodedBodySize || 0), 0);

  // --- bölümler: main (yoksa body) altındaki üst düzey içerik blokları, sırayla
  const root = document.querySelector('main') || document.body;
  let blocks = [...root.querySelectorAll(':scope > section, :scope > div > section, :scope > *')]
    .filter((el) => !el.matches(CHROME) && !el.closest(CHROME) && visible(el) && el.getBoundingClientRect().height > 80);
  blocks = blocks.filter((el, i) => !blocks.some((o, j) => j !== i && o.contains(el)));
  const PRICE = /(?:₺|\bTL\b|\$|€|£)\s?\d|\d[\d.,]*\s?(?:₺|\bTL\b|€|\$|£)/;
  const sections = blocks.slice(0, 25).map((el) => {
    const h = el.querySelector('h1, h2, h3');
    const imgs = [...el.querySelectorAll('img, svg')].filter(visible);
    const small = imgs.filter((i) => { const r = i.getBoundingClientRect(); return r.height < 90 && r.width < 240; });
    const text = el.innerText || '';
    return {
      heading: fold(short(h ? h.innerText : '', 80)),
      classes: fold(short(cls(el) + ' ' + (el.id || ''), 120)),
      h1: !!el.querySelector('h1'),
      images: imgs.length,
      logo_like: small.length,
      quotes: el.querySelectorAll('blockquote, [class*=testimonial], [class*=review], [class*=quote]').length,
      stars: (text.match(/★/g) || []).length + el.querySelectorAll('[class*=star], [aria-label*=star i]').length,
      prices: (text.match(new RegExp(PRICE.source, 'g')) || []).length,
      details: el.querySelectorAll('details, [aria-expanded]').length,
      questions: [...el.querySelectorAll('h2, h3, h4, summary, dt, button')].filter((q) => /\?\s*$/.test(q.innerText || '')).length,
      buttons: [...el.querySelectorAll('a[href], button')].filter((b) => /btn|button|cta/.test(cls(b))).length,
      form: !!el.querySelector('form input:not([type=hidden])'),
      words: words(text),
      top: Math.round(el.getBoundingClientRect().top + scrollY),
    };
  });

  // --- hero
  const h1 = document.querySelector('h1');
  const h1Text = h1 ? short(h1.innerText, 200) : '';
  let sub = false;
  if (h1) {
    let n = h1.nextElementSibling;
    for (let i = 0; n && i < 3; n = n.nextElementSibling, i++) {
      if (/^(P|H2|H3|DIV)$/.test(n.tagName) && words(n.innerText) >= 5) { sub = true; break; }
    }
  }
  const above = [...document.querySelectorAll('a[href], button')].filter((b) => {
    if (b.closest(CHROME) || !visible(b) || b.type === 'submit') return false;
    const r = b.getBoundingClientRect();
    return r.top < vh && /btn|button|cta|primary/.test(cls(b));
  });
  let media = 'none';
  for (const el of document.querySelectorAll('video, iframe[src*=youtube], iframe[src*=vimeo], img, svg, canvas')) {
    const r = el.getBoundingClientRect();
    if (r.top >= vh || r.bottom <= 0 || !visible(el) || el.closest(CHROME)) continue;
    if (r.width * r.height < vw * vh * 0.08) continue;
    const tag = el.tagName;
    media = tag === 'VIDEO' || tag === 'IFRAME' ? 'video'
      : tag === 'SVG' || tag === 'CANVAS' || /\.svg(\?|$)/i.test(el.currentSrc || el.src || '') ? 'illustration' : 'image';
    break;
  }

  // --- güven sinyalleri ve sayfa metni (kelime kararları Python'da)
  const claims = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(), i = 0; n && i < 6000 && claims.length < 12; n = walker.nextNode(), i++) {
    const t = n.textContent;
    if (t && t.length < 120 && /\d[\d.,]*\s?(\+|k\b|m\b|bin|milyon|million|thousand)/i.test(t)) claims.push(fold(short(t, 120)));
  }
  const footer = document.querySelector('footer');
  const alts = [...document.querySelectorAll('img[alt]')].map((i) => fold(i.alt)).filter(Boolean).slice(0, 80);
  const links = [], external = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    let u; try { u = new URL(a.getAttribute('href'), location.href); } catch (e) { continue; }
    if (u.protocol === 'mailto:' || u.protocol === 'tel:') { links.push(u.protocol); continue; }
    if (!/^https?:$/.test(u.protocol)) continue;
    if (u.origin === location.origin) { const p = u.origin + u.pathname; if (!links.includes(p) && links.length < 200) links.push(p); }
    else external.add(u.hostname);
  }
  const meta = (p) => { const m = document.querySelector(`meta[property="${p}"], meta[name="${p}"]`); return m ? (m.content || '') : ''; };
  const stack = [];
  if (window.__NEXT_DATA__ || document.querySelector('#__next, script[src*="/_next/"]')) stack.push('nextjs');
  if (document.querySelector('[data-wf-site]')) stack.push('webflow');
  if (document.querySelector('link[href*="wp-content"], script[src*="wp-content"]')) stack.push('wordpress');
  if (window.Shopify) stack.push('shopify');
  if (document.querySelector('[data-framer-name], script[src*="framer"]')) stack.push('framer');
  if (document.querySelector('#__nuxt, #__nuxt-app')) stack.push('nuxt');
  if (document.querySelector('script[type=module][src*="/assets/index-"]')) stack.push('vite');
  return {
    perf: {lcp_ms: lcp === null ? null : Math.round(lcp), cls: cls_ === null ? 0 : Math.round(cls_ * 1000) / 1000,
           js_kb: Math.round(jsBytes / 1024)},
    sections,
    hero: {headline_words: words(h1Text), subheadline: sub, cta_above_fold: above.length,
           primary_cta: above.length ? short(above[0].innerText || above[0].getAttribute('aria-label') || '', 60) : '',
           media},
    seo: {title_length: (document.title || '').length, h1_words: words(h1Text),
          h2_count: document.querySelectorAll('h2').length,
          word_count: words(document.body ? document.body.innerText : '')},
    claims, alts,
    footer_text: fold(short(footer ? footer.innerText : '', 1500)),
    links, external_hosts: [...external].slice(0, 100),
    og_image: !!meta('og:image'),
    og_size: [parseInt(meta('og:image:width')) || null, parseInt(meta('og:image:height')) || null],
    stack,
  };
}
