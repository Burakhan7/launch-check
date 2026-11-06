/** Kanıt (evidence) nesnesini ekranda gösterilebilir satırlara çevirir. Saf fonksiyonlar. */

export type EvidenceValue =
  | { kind: "text"; value: string }
  | { kind: "code"; value: string }
  | { kind: "list"; items: string[]; more: number };

export interface EvidenceRow {
  key: string;
  value: EvidenceValue;
}

const MAX_ITEMS = 10;
// Değeri kod/teknik ifade olarak gösterilecek alanlar (monospace)
const CODE_KEYS = new Set(["masked", "path", "file", "attribute", "common_name", "project_ref"]);
// Satır olarak gösterilmeyen yardımcı alanlar (sayılar listelerin "+N daha" bilgisinde kullanılır)
const HIDDEN_KEYS = new Set(["count", "location_count", "kind", "searched"]);

function isLocation(v: unknown): v is { file: string; line: number } {
  return typeof v === "object" && v !== null && "file" in v && "line" in v;
}

function isComponent(v: unknown): v is { type: string; name: string } {
  return typeof v === "object" && v !== null && "type" in v && "name" in v;
}

function stringify(v: unknown): string {
  if (isLocation(v)) return `${v.file}:${v.line}`;
  if (isComponent(v)) return `${v.type} ${v.name}`;
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function totalFor(key: string, evidence: Record<string, unknown>, shown: number): number {
  const total =
    key === "locations"
      ? evidence.location_count
      : key === "urls" || key === "components"
        ? evidence.count
        : undefined;
  return typeof total === "number" ? Math.max(0, total - shown) : 0;
}

export function evidenceRows(
  evidence: Record<string, unknown>,
  translateValue: (key: string, value: string) => string = (_k, v) => v,
): EvidenceRow[] {
  const rows: EvidenceRow[] = [];
  for (const [key, raw] of Object.entries(evidence)) {
    if (HIDDEN_KEYS.has(key) || raw === null || raw === undefined) continue;
    if (Array.isArray(raw)) {
      if (raw.length === 0) continue;
      const items = raw.slice(0, MAX_ITEMS).map((v) => translateValue(key, stringify(v)));
      rows.push({
        key,
        value: { kind: "list", items, more: totalFor(key, evidence, items.length) },
      });
    } else if (typeof raw === "object") {
      const items = Object.entries(raw as Record<string, unknown>).map(
        ([k, v]) => `${k}: ${stringify(v)}`,
      );
      if (items.length) rows.push({ key, value: { kind: "list", items, more: 0 } });
    } else {
      const value = translateValue(key, String(raw));
      rows.push({
        key,
        value: CODE_KEYS.has(key) ? { kind: "code", value } : { kind: "text", value },
      });
    }
  }
  return rows;
}
