// Çerez banner'ı tespiti ve "Reddet" / "Ayarlar" düğmesinin işaretlenmesi
// (launchcheck/deep/browser/consent.py). Sadece okur; hedef düğmeye `data-lc-consent` özniteliği
// ekler, tıklamayı Playwright yapar. Form alanı içeren kapsayıcılardaki düğmelere dokunulmaz.
// opts: {words: {banner, reject, settings, accept}, cmps: [{name, banner, reject}], target}
(opts) => {
  const fold = (s) => (s || '').replace(/[ıİ]/g, 'i').replace(/[şŞ]/g, 's').replace(/[ğĞ]/g, 'g')
    .replace(/[üÜ]/g, 'u').replace(/[öÖ]/g, 'o').replace(/[çÇ]/g, 'c').replace(/[âÂ]/g, 'a')
    .replace(/[îÎ]/g, 'i').replace(/[ûÛ]/g, 'u').toLowerCase().replace(/\s+/g, ' ').trim();
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Python'daki keywords.compile_words ile aynı kural: "*" ile biten öğe ön ektir
  const compile = (list) => {
    const parts = [...new Set((list || []).map(fold))].sort((a, b) => b.length - a.length)
      .map((w) => (w.endsWith('*') ? esc(w.slice(0, -1)) : esc(w) + '(?![a-z0-9])'));
    return parts.length ? new RegExp('(?<![a-z0-9])(?:' + parts.join('|') + ')') : null;
  };
  const rx = {};
  for (const key of ['banner', 'reject', 'settings', 'accept']) rx[key] = compile(opts.words[key]);
  const test = (key, text) => !!(rx[key] && rx[key].test(text));

  const all = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (all.length > 20000) return;
      all.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  const deepQuery = (selector) => {
    if (!selector) return null;
    try {
      const direct = document.querySelector(selector);
      if (direct) return direct;
      for (const el of all) if (el.shadowRoot) { const hit = el.shadowRoot.querySelector(selector); if (hit) return hit; }
    } catch (e) { /* geçersiz seçici */ }
    return null;
  };
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const deepVisible = (el) => visible(el)
    || !!(el.shadowRoot && [...el.shadowRoot.querySelectorAll('*')].some(visible))
    || [...el.querySelectorAll('*')].slice(0, 300).some(visible);
  const text = (el) => fold((el.innerText || el.textContent || '').slice(0, 3000));
  const BUTTONS = 'button, a, [role=button], input[type=button], input[type=submit]';
  const label = (el) => fold(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '');
  const insideDataForm = (el) => {
    const form = el.closest('form');
    return !!(form && form.querySelector('input[type=text], input[type=email], input[type=password], input[type=tel], input[type=search], textarea'));
  };
  const buttonsIn = (root) => {
    const found = [...root.querySelectorAll(BUTTONS)];
    if (root.shadowRoot) found.push(...root.shadowRoot.querySelectorAll(BUTTONS));
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) found.push(...el.shadowRoot.querySelectorAll(BUTTONS));
    return found.filter((b) => visible(b) && !insideDataForm(b));
  };
  const short = (l) => l.length > 0 && l.length <= 60 && l.split(' ').length <= 7;

  // 1) Bilinen araçlar
  let cmp = null, cmpVisible = false;
  const containers = [];
  for (const c of opts.cmps || []) {
    const el = deepQuery(c.banner);
    if (!el) continue;
    cmp = cmp || c.name;
    if (deepVisible(el)) { cmpVisible = true; containers.push({el, rejectSel: c.reject}); }
  }
  // 2) Genel banner: sabit/diyalog katman veya çerez/onay adlı kapsayıcı + çerez kelimesi +
  //    kabul/reddet/ayarlar düğmesi
  const KEY = /cookie|consent|gdpr|kvkk|cerez|çerez|cmp|privacy|gizlilik/i;
  const isLayer = (el) => {
    const s = getComputedStyle(el);
    return s.position === 'fixed' || s.position === 'sticky' || el.tagName === 'DIALOG'
      || ['dialog', 'alertdialog'].includes(el.getAttribute('role')) || el.getAttribute('aria-modal') === 'true';
  };
  const hinted = (el) => KEY.test((el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '')
    + ' ' + (el.getAttribute('aria-label') || ''));
  let generic = false;
  for (const el of all) {
    if (containers.length >= 6) break;
    if (!(isLayer(el) || hinted(el)) || !visible(el)) continue;
    const t = text(el);
    if (!t || t.length > 3000 || !test('banner', t)) continue;
    const labels = buttonsIn(el).map(label).filter(short);
    if (!labels.some((l) => test('accept', l) || test('reject', l) || test('settings', l))) continue;
    if (containers.some((c) => c.el.contains(el) || el.contains(c.el))) continue;
    containers.push({el, rejectSel: ''});
    generic = true;
  }
  const banner = cmpVisible || generic;
  const pick = (target) => {
    for (const c of containers) {
      if (target === 'reject' && c.rejectSel) {
        const el = deepQuery(c.rejectSel);
        if (el && visible(el)) return el;
      }
      for (const b of buttonsIn(c.el)) {
        const l = label(b);
        if (short(l) && test(target, l) && (target !== 'settings' || !test('accept', l))) return b;
      }
    }
    return null;
  };
  const result = {cmp, banner, cmpVisible, marked: false, label: '',
                  labels: containers.flatMap((c) => buttonsIn(c.el).map(label).filter(short)).slice(0, 8)};
  if (opts.target) {
    const el = pick(opts.target);
    if (el) {
      el.setAttribute('data-lc-consent', opts.target);
      result.marked = true;
      result.label = label(el).slice(0, 60);
    }
  }
  return result;
}
