// Sayfanın zaten yüklediği aynı origin JS dosyalarında yönetim rotası adayları. Dosyalar tarayıcı
// önbelleğinden okunur (proxy üzerinden); sadece eşleşen yol şablonları döner, kod dönmez.
async (opts) => {
  const RX = /["'`](\/(?:admin|yonetim|panel|backoffice|dashboard\/admin|cms)(?:\/[a-z0-9_-]+){0,2})\/?["'`]/gi;
  const urls = new Set();
  for (const s of document.scripts) if (s.src) urls.add(s.src);
  for (const e of performance.getEntriesByType('resource')) {
    if (e.initiatorType === 'script' || /\.m?js(\?|$)/.test(e.name)) urls.add(e.name);
  }
  const same = [...urls].filter((u) => { try { return new URL(u).origin === location.origin; } catch (e) { return false; } })
    .slice(0, opts.maxFiles);
  const found = new Set();
  for (const u of same) {
    try {
      const text = await (await fetch(u, {cache: 'force-cache'})).text();
      for (const m of text.slice(0, opts.maxBytes).matchAll(RX)) found.add(m[1].toLowerCase());
    } catch (e) { /* atla */ }
    if (found.size >= opts.maxRoutes) break;
  }
  return [...found].slice(0, opts.maxRoutes);
}
