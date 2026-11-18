/** Tüm bulguların düzeltme prompt'larını öncelik sırasıyla tek bir metinde birleştirir.
 * Saf fonksiyon: kullanıcı bunu tek seferde AI aracına (Lovable, Bolt, Cursor...) yapıştırır. */

import type { Category, Finding, Severity } from "./api";
import { evidenceLocations } from "./evidence";

export const DEFAULT_MAX_CHARS = 12000;
const MAX_LOCATIONS = 3;

const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];
const CATEGORY_ORDER: Category[] = [
  "security",
  "prod_readiness",
  "store_compliance",
  "conversion",
  "performance",
];

export type TranslateFn = (key: string, values?: Record<string, string | number>) => string;

export interface CombinedPromptOptions {
  target: string;
  platforms: string[];
  /** "CombinedPrompt" namespace'i */
  t: TranslateFn;
  /** Önem etiketleri (mevcut "Severity" çevirileri); verilmezse ham değer */
  severityLabel?: (severity: Severity) => string;
  maxChars?: number;
}

export interface CombinedPrompt {
  text: string;
  included: number;
  omitted: number;
}

/** Önem (critical → low), sonra kategori, sonra API sırası. `info` dahil edilmez. */
export function orderFindings(findings: Finding[]): Finding[] {
  return findings
    .map((finding, index) => ({ finding, index }))
    .filter(({ finding }) => finding.severity !== "info")
    .sort(
      (a, b) =>
        SEVERITY_ORDER.indexOf(a.finding.severity) - SEVERITY_ORDER.indexOf(b.finding.severity) ||
        CATEGORY_ORDER.indexOf(a.finding.category) - CATEGORY_ORDER.indexOf(b.finding.category) ||
        a.index - b.index,
    )
    .map(({ finding }) => finding);
}

function block(
  finding: Finding,
  n: number,
  t: TranslateFn,
  severityLabel: (s: Severity) => string,
): string {
  const lines = [`## ${n}. ${finding.title}  [${severityLabel(finding.severity)}]`];
  const locations = evidenceLocations(finding.evidence ?? {}, MAX_LOCATIONS);
  if (locations.length) lines.push(t("location", { value: locations.join(", ") }));
  lines.push(finding.fix_prompt.trim());
  return lines.join("\n");
}

export function buildCombinedPrompt(
  findings: Finding[],
  opts: CombinedPromptOptions,
): CombinedPrompt {
  const ordered = orderFindings(findings);
  if (ordered.length === 0) return { text: "", included: 0, omitted: 0 };

  const maxChars = opts.maxChars ?? DEFAULT_MAX_CHARS;
  const severityLabel = opts.severityLabel ?? ((s: Severity) => s);
  const platforms = opts.platforms.filter(Boolean);
  const header = opts.t("header", {
    target: opts.target,
    platforms: platforms.length ? opts.t("platforms", { list: platforms.join(", ") }) : "",
  });
  const footer = opts.t("footer");
  const truncated = (count: number) => opts.t("truncated", { count });
  const join = (parts: string[]) => parts.join("\n\n");

  const blocks: string[] = [];
  for (let i = 0; i < ordered.length; i++) {
    const candidate = [...blocks, block(ordered[i], i + 1, opts.t, severityLabel)];
    const rest = ordered.length - candidate.length;
    const tail = rest > 0 ? [footer, truncated(rest)] : [footer];
    // Bir bulgu yarıdan kesilmez; sığmıyorsa eklenmez (ilk bulgu her zaman eklenir).
    if (blocks.length > 0 && join([header, ...candidate, ...tail]).length > maxChars) break;
    blocks.push(candidate[candidate.length - 1]);
  }

  const omitted = ordered.length - blocks.length;
  const parts = [header, ...blocks, footer];
  if (omitted > 0) parts.push(truncated(omitted));
  return { text: join(parts), included: blocks.length, omitted };
}
