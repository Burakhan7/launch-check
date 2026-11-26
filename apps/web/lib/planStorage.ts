/** Öncelik planındaki işaretler sadece bu tarayıcıda (localStorage) tutulur; sunucuya gitmez.
 * Depolama kapalı veya hatalıysa (gizli mod, kota, bozuk JSON) liste işaretsiz başlar. */
const PREFIX = "lc-plan-";

export function readDone(storage: Pick<Storage, "getItem"> | undefined, scanId: string): string[] {
  try {
    const raw = storage?.getItem(PREFIX + scanId);
    const value: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

export function writeDone(
  storage: Pick<Storage, "setItem"> | undefined,
  scanId: string,
  ids: string[],
): boolean {
  try {
    storage?.setItem(PREFIX + scanId, JSON.stringify(ids));
    return true;
  } catch {
    return false;
  }
}
