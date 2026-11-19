// Storage ölçümü: değerler sayfada kalır; sadece ad, uzunluk ve JWT bitiş süresi döner.
() => {
  const JWT = /eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*/g;
  const expDays = (token) => {
    try {
      const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const payload = JSON.parse(atob(part + '='.repeat((4 - part.length % 4) % 4)));
      return typeof payload.exp === 'number' ? (payload.exp * 1000 - Date.now()) / 86400000 : null;
    } catch (e) { return null; }
  };
  const out = [];
  for (const [area, store] of [['local', window.localStorage], ['session', window.sessionStorage]]) {
    let keys = [];
    try { keys = Object.keys(store); } catch (e) { continue; }
    for (const key of keys.slice(0, 100)) {
      const value = store.getItem(key) || '';
      const tokens = value.match(JWT) || [];
      const exps = tokens.map(expDays).filter((d) => d !== null);
      out.push({area, key: key.slice(0, 100), length: value.length, jwt: tokens.length > 0,
                expDays: exps.length ? Math.max(...exps) : null});
    }
  }
  return out;
}
