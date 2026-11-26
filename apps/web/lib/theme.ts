/** Tema tercihi: "light" | "dark" | null (sistem). Sadece tarayıcının localStorage'ında
 * tutulur; sunucuya gitmez. Depolama kapalıysa (gizli mod, izin yok) sessizce sistem teması. */
export type ThemeChoice = "light" | "dark";
export const THEME_KEY = "lc-theme";

export function readTheme(storage: Pick<Storage, "getItem"> | undefined): ThemeChoice | null {
  try {
    const value = storage?.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function writeTheme(
  storage: Pick<Storage, "setItem" | "removeItem"> | undefined,
  value: ThemeChoice | null,
): void {
  try {
    if (value) storage?.setItem(THEME_KEY, value);
    else storage?.removeItem(THEME_KEY);
  } catch {
    // depolama yok: tercih bu sayfa için uygulanır, kalıcı olmaz
  }
}

/** Seçim yoksa sistem tercihi; `data-theme` her zaman çözülmüş değeri taşır. */
export function applyTheme(value: ThemeChoice | null): void {
  const dark =
    value === "dark" ||
    (value === null && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

/** İlk boyamadan önce çalışan satır içi betik (layout <head>): yanıp sönmeyi önler. */
export const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_KEY}");if(t!=="light"&&t!=="dark"){t=window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}document.documentElement.dataset.theme=t}catch(e){}})();`;
