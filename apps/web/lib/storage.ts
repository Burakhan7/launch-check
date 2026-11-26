/** Tarayıcı localStorage'ı; yoksa veya erişim engelliyse (ör. Safari'de çerezler kapalıyken
 * `window.localStorage` okumak SecurityError atar) undefined. Sunucuda da undefined. */
export function browserStorage(): Storage | undefined {
  try {
    return typeof window === "undefined" ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}
