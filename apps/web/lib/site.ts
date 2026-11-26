/** Site geneli ayarlar. NEXT_PUBLIC_ değişkenleri build sırasında gömülür. */
export const CONTACT_URL =
  process.env.NEXT_PUBLIC_CONTACT_URL || "mailto:merhaba@launchcheck.example";
export const POLL_INTERVAL_MS = 2000;
export const MAX_APK_BYTES = 150 * 1024 * 1024;
