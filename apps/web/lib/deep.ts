/** Faz 2 (derin tarama) raporu için saf yardımcılar. React'e bağımlı değil; testler doğrudan
 * çağırır. Metinler çeviri fonksiyonuyla gelir, burada sabit metin yok. */

import type { DeepAsset, Finding, ScanReport } from "./api";
import type { TranslateFn } from "./combinedPrompt";

export const DEEP_PARENT_MAX_AGE_MS = 24 * 60 * 60 * 1000;
export const MAX_COMPETITORS = 3;
export const DEEP_MODULES = [
  "render",
  "privacy",
  "funnel",
  "benchmark",
  "perf",
  "external",
  "links",
] as const;

/** Faz 1 raporundan derin tarama başlatılabilir mi? (API'nin ön koşullarıyla aynı) */
export function canStartDeep(report: ScanReport, now: number = Date.now()): boolean {
  if (report.phase !== 1 || report.target_type !== "web" || report.status !== "done") return false;
  return now - new Date(report.created_at).getTime() < DEEP_PARENT_MAX_AGE_MS;
}

export interface PlanGroups {
  today: Finding[];
  week: Finding[];
  month: Finding[];
}

/** API'nin öncelik planındaki kimlikleri bulgulara çevirir (bilinmeyen kimlik atlanır). */
export function planGroups(report: ScanReport): PlanGroups {
  const byId = new Map(report.findings.filter((f) => f.id).map((f) => [f.id as string, f]));
  const plan = report.sections?.plan ?? { today: [], week: [], month: [] };
  const pick = (ids: string[]) => ids.map((id) => byId.get(id)).filter((f): f is Finding => !!f);
  return { today: pick(plan.today), week: pick(plan.week), month: pick(plan.month) };
}

export function phase1Findings(report: ScanReport): Finding[] {
  return report.findings.filter((f) => (f.source_phase ?? 1) === 1);
}

export function phase2Findings(report: ScanReport): Finding[] {
  return report.findings.filter((f) => f.source_phase === 2);
}

/** Ana sayfanın belirli türdeki ekran görüntüsü (render: screenshot_mobile/desktop/nojs). */
export function homeAsset(assets: DeepAsset[], kind: string, target: string): DeepAsset | null {
  const matches = assets.filter((a) => a.kind === kind);
  const home = matches.find((a) => {
    try {
      const path = new URL(a.page_url).pathname;
      return (
        path === "/" || path === "" || a.page_url.replace(/\/$/, "") === target.replace(/\/$/, "")
      );
    } catch {
      return false;
    }
  });
  return home ?? matches[0] ?? null;
}

export function seconds(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return (ms / 1000).toFixed(1);
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** "Landing'ini yeniden kur" prompt'u (PHASE2 §7.5): ortak bölüm sırası + kullanıcının kendi
 * bilgileri. Örnek sitelerin metni hiç kullanılmaz (veri katmanında da yok). */
export function rebuildPrompt(
  sections: string[],
  own: { target: string; title: string },
  t: TranslateFn,
  sectionLabel: (key: string) => string,
): string {
  const lines = [t("rebuildIntro", { target: own.target, title: own.title || own.target })];
  sections.forEach((key, i) => {
    lines.push(`${i + 1}. ${sectionLabel(key)}: ${t(`rebuildSection.${key}`)}`);
  });
  lines.push(t("rebuildRules"));
  return lines.join("\n");
}

/** Tarama sürerken sekme başlığı ve (izin verildiyse) tarayıcı bildirimi. Sunucuya hiçbir şey
 * gönderilmez; Notification API tamamen tarayıcıda çalışır. */
export function notifyDone(title: string, body: string): void {
  if (typeof window === "undefined") return;
  document.title = title;
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      new Notification(title, { body });
    } catch {
      // bazı tarayıcılar (iOS) sayfa içinden bildirime izin vermez
    }
  }
}
