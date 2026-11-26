/** Launch Check API istemcisi. İstekler aynı origin'e gider (/api/...): prod'da Caddy,
 * geliştirmede Next.js rewrite'ı API'ye yönlendirir. */

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type Category =
  "security" | "prod_readiness" | "performance" | "conversion" | "store_compliance";
export type ScanStatus = "queued" | "running" | "done" | "failed";
export type ScoreLabel = "not_ready" | "needs_work" | "good";
export type TargetType = "web" | "apk";

export type Verification = "confirmed" | "refuted" | "escalated" | "not_verifiable";
export type Ease = "easy" | "moderate" | "hard";

export interface Impact {
  who: string;
  ease: Ease;
  consequence: string;
  legal: string | null;
  timeline: { when: "today" | "week" | "month"; text: string }[];
  fix_minutes: number;
}

export interface Finding {
  id?: string | null;
  check_id: string;
  severity: Severity;
  category: Category;
  title: string;
  description: string;
  why_it_matters: string;
  fix_steps: string[];
  fix_prompt: string;
  evidence: Record<string, unknown>;
  // Faz 2
  source_phase?: number;
  verification?: Verification | null;
  module?: string | null;
  impact?: Impact | null;
  fingerprint?: string;
}

export interface PassedCheck {
  check_id: string;
  category: Category;
  title: string;
}

export interface ScanReport {
  scan_id: string;
  status: ScanStatus;
  target_type: TargetType;
  target_label: string;
  phase: number;
  score: number | null;
  score_label: ScoreLabel | null;
  category_scores: Record<string, number> | null;
  meta: Record<string, unknown>;
  error: { code: string; message: string } | null;
  findings: Finding[];
  passed: PassedCheck[];
  created_at: string;
  finished_at: string | null;
  expires_at: string;
  // Faz 2
  parent_scan_id?: string | null;
  progress?: { modules?: Record<string, { status: ModuleStatus; found: number }> } | null;
  queue_position?: number | null;
  eta_s?: number | null;
  sections?: DeepSections | null;
}

export type ModuleStatus = "pending" | "running" | "done" | "partial" | "failed";

export interface DeepAsset {
  id: string;
  kind: string;
  page_url: string;
  width: number;
  height: number;
}

export interface DeepSections {
  phase_diff: {
    phase1: { findings: number; score: number | null };
    phase2: {
      new_findings: number;
      verification: Record<Verification, number>;
      forgotten_subdomains: number;
      untested_gates: number;
      score: number | null;
    };
    score_dropped: boolean;
  };
  plan: { today: string[]; week: string[]; month: string[] };
  gates: Gates;
  rescan: RescanDiff | null;
  assets: DeepAsset[];
}

export interface Gates {
  api_endpoints?: { method: string; host: string; path: string }[];
  supabase?: { projects: string[]; tables: string[]; buckets: string[] };
  firebase?: { projects: string[] };
  auth_endpoints?: string[];
  webhooks?: string[];
  personal_data_forms?: string[];
  admin_unverified?: string[];
  total?: number;
}

export interface RescanDiff {
  compare_to: string;
  score_before: number | null;
  score_after: number | null;
  closed: { check_id: string; title: string; severity: Severity }[];
  new: { check_id: string; title: string; severity: Severity }[];
  persisting: { check_id: string; title: string; severity: Severity }[];
}

export interface DeepEvent {
  id: number;
  module: string;
  text: string;
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

const NETWORK_ERROR = "network_error";

export async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } };
    if (body.error?.code) {
      return new ApiError(body.error.code, body.error.message ?? "", res.status);
    }
  } catch {
    // JSON olmayan yanıt (ör. proxy hatası)
  }
  return new ApiError("internal_error", "", res.status);
}

async function request<T>(input: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(input, { ...init, cache: "no-store" });
  } catch {
    throw new ApiError(NETWORK_ERROR, "", 0);
  }
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}

export function startWebScan(url: string, lang: string): Promise<{ scan_id: string }> {
  return request(`/api/scans/web?lang=${lang}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url }),
  });
}

export function getScan(id: string, lang: string): Promise<ScanReport> {
  return request(`/api/scans/${encodeURIComponent(id)}?lang=${lang}`);
}

/** APK yükleme: fetch yükleme ilerlemesi vermediği için XMLHttpRequest. */
export function startApkScan(
  file: File,
  lang: string,
  onProgress: (fraction: number) => void,
): Promise<{ scan_id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);
    form.append("consent", "true");
    xhr.open("POST", `/api/scans/apk?lang=${lang}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(NETWORK_ERROR, "", 0));
    xhr.onload = async () => {
      const res = new Response(xhr.responseText, {
        status: xhr.status,
        headers: { "content-type": "application/json" },
      });
      if (xhr.status >= 200 && xhr.status < 300) resolve(await res.json());
      else reject(await parseError(res));
    };
    xhr.send(form);
  });
}

export interface DeepScanOptions {
  competitors: string[];
  use_reference: boolean;
  category?: string | null;
  compare_to?: string | null;
}

export function startDeepScan(
  parentId: string,
  options: DeepScanOptions,
  lang: string,
): Promise<{ scan_id: string; queue_position: number; eta_s: number }> {
  return request(`/api/scans/${encodeURIComponent(parentId)}/deep?lang=${lang}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(options),
  });
}

export function getEvents(
  id: string,
  after: number,
  lang: string,
): Promise<{ events: DeepEvent[]; status: ScanStatus }> {
  return request(`/api/scans/${encodeURIComponent(id)}/events?after=${after}&lang=${lang}`);
}

export function getCategories(
  lang: string,
): Promise<{ categories: { key: string; label: string }[] }> {
  return request(`/api/benchmark/categories?lang=${lang}`);
}

/** Ekran görüntüsü adresi (aynı origin; dosya API'den gelir, dış siteden değil). */
export function assetUrl(scanId: string, assetId: string): string {
  return `/api/scans/${encodeURIComponent(scanId)}/assets/${encodeURIComponent(assetId)}`;
}

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];
