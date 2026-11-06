/** Launch Check API istemcisi. İstekler aynı origin'e gider (/api/...): prod'da Caddy,
 * geliştirmede Next.js rewrite'ı API'ye yönlendirir. */

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type Category =
  "security" | "prod_readiness" | "performance" | "conversion" | "store_compliance";
export type ScanStatus = "queued" | "running" | "done" | "failed";
export type ScoreLabel = "not_ready" | "needs_work" | "good";
export type TargetType = "web" | "apk";

export interface Finding {
  check_id: string;
  severity: Severity;
  category: Category;
  title: string;
  description: string;
  why_it_matters: string;
  fix_steps: string[];
  fix_prompt: string;
  evidence: Record<string, unknown>;
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

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];
