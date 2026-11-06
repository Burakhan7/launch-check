"use client";

import { useLocale, useTranslations } from "next-intl";
import {
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type DragEvent,
  type FormEvent,
} from "react";
import { useRouter } from "@/i18n/navigation";
import { ApiError, startApkScan, startWebScan } from "@/lib/api";
import { MAX_APK_BYTES } from "@/lib/site";

type Tab = "web" | "apk";

function useErrorText() {
  const t = useTranslations("Errors");
  return (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.code === "network_error") return t("network_error");
      return err.message || t("generic");
    }
    return t("generic");
  };
}

// Rapor sayfasındaki "Yeni APK yükle" linki /#apk ile gelir.
function subscribeHash(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function useHashWantsApk(): boolean {
  return useSyncExternalStore(
    subscribeHash,
    () => window.location.hash === "#apk",
    () => false,
  );
}

export function ScanForm({ initialTab = "web" }: { initialTab?: Tab }) {
  const t = useTranslations("ScanForm");
  const hashWantsApk = useHashWantsApk();
  const [chosen, setTab] = useState<Tab | null>(null);
  const tab: Tab = chosen ?? (hashWantsApk ? "apk" : initialTab);
  return (
    <div
      id="apk"
      className="w-full scroll-mt-24 rounded-3xl border border-line bg-background p-2 shadow-[0_20px_60px_-25px_color-mix(in_srgb,var(--brand)_45%,transparent)]"
    >
      <div role="tablist" className="grid grid-cols-2 gap-1 rounded-2xl bg-surface p-1">
        {(["web", "apk"] as const).map((key) => (
          <button
            key={key}
            role="tab"
            type="button"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`rounded-xl px-2 py-2.5 text-sm font-semibold whitespace-nowrap transition sm:px-4 ${
              tab === key
                ? "bg-brand text-brand-contrast shadow-sm"
                : "text-muted hover:text-foreground"
            }`}
          >
            {key === "web" ? t("tabWeb") : t("tabApk")}
          </button>
        ))}
      </div>
      <div className="p-4 sm:p-6">{tab === "web" ? <WebForm /> : <ApkForm />}</div>
    </div>
  );
}

function WebForm() {
  const t = useTranslations("ScanForm");
  const locale = useLocale();
  const router = useRouter();
  const errorText = useErrorText();
  const inputId = useId();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!url.trim()) return setError(t("needUrl"));
    setBusy(true);
    setError(null);
    try {
      const { scan_id } = await startWebScan(url.trim(), locale);
      router.push(`/report/${scan_id}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3">
      <label htmlFor={inputId} className="text-sm font-medium">
        {t("urlLabel")}
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id={inputId}
          type="url"
          inputMode="url"
          autoComplete="url"
          placeholder={t("urlPlaceholder")}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-4 py-3 text-base outline-none transition placeholder:text-muted/70 focus:border-brand focus:bg-background"
        />
        <SubmitButton busy={busy} label={t("scanButton")} busyLabel={t("scanning")} />
      </div>
      <FormError message={error} />
      <p className="text-xs text-muted">{t("passiveNote")}</p>
    </form>
  );
}

function ApkForm() {
  const t = useTranslations("ScanForm");
  const locale = useLocale();
  const router = useRouter();
  const errorText = useErrorText();
  const fileInput = useRef<HTMLInputElement>(null);
  const consentId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  function pick(candidate: File | undefined) {
    setError(null);
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith(".apk")) return setError(t("wrongType"));
    if (candidate.size > MAX_APK_BYTES) return setError(t("tooLarge"));
    setFile(candidate);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files[0]);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) return fileInput.current?.click();
    if (!consent) return setError(t("needConsent"));
    setError(null);
    setProgress(0);
    try {
      const { scan_id } = await startApkScan(file, locale, setProgress);
      router.push(`/report/${scan_id}`);
    } catch (err) {
      setError(errorText(err));
      setProgress(null);
    }
  }

  const busy = progress !== null;
  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-4 py-8 text-center transition ${
          dragging ? "border-brand bg-brand-soft" : "border-line bg-surface"
        }`}
      >
        {file ? (
          <>
            <ApkIcon />
            <p className="max-w-full truncate font-medium">{file.name}</p>
            <p className="text-xs text-muted">{(file.size / 1024 / 1024).toFixed(1)} MB</p>
            {!busy && (
              <button
                type="button"
                onClick={() => setFile(null)}
                className="text-sm text-brand underline-offset-4 hover:underline"
              >
                {t("removeFile")}
              </button>
            )}
          </>
        ) : (
          <>
            <ApkIcon />
            <p className="font-medium">{t("dropTitle")}</p>
            <p className="text-sm text-muted">{t("dropOr")}</p>
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="rounded-full border border-brand px-4 py-1.5 text-sm font-semibold text-brand transition hover:bg-brand hover:text-brand-contrast"
            >
              {t("chooseFile")}
            </button>
            <p className="text-xs text-muted">{t("fileHint")}</p>
          </>
        )}
        <input
          ref={fileInput}
          type="file"
          accept=".apk,application/vnd.android.package-archive"
          className="hidden"
          onChange={(e) => pick(e.target.files?.[0])}
        />
      </div>

      <label htmlFor={consentId} className="flex cursor-pointer items-start gap-3 text-sm">
        <input
          id={consentId}
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-[var(--brand)]"
        />
        <span>{t("consent")}</span>
      </label>

      {busy && (
        <div className="flex flex-col gap-1.5">
          <div className="h-2 overflow-hidden rounded-full bg-surface-strong">
            <div
              className="h-full rounded-full bg-brand transition-[width]"
              style={{ width: `${Math.round((progress ?? 0) * 100)}%` }}
            />
          </div>
          <p className="text-xs text-muted">
            {t("uploading", { percent: Math.round((progress ?? 0) * 100) })}
          </p>
        </div>
      )}
      <FormError message={error} />
      <SubmitButton busy={busy} label={t("scanButton")} busyLabel={t("scanning")} />
    </form>
  );
}

function SubmitButton({
  busy,
  label,
  busyLabel,
}: {
  busy: boolean;
  label: string;
  busyLabel: string;
}) {
  return (
    <button
      type="submit"
      disabled={busy}
      className="rounded-xl bg-brand px-6 py-3 font-semibold text-brand-contrast shadow-sm transition hover:bg-brand-strong disabled:cursor-wait disabled:opacity-70"
    >
      {busy ? busyLabel : label}
    </button>
  );
}

function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-lg bg-sev-critical-soft px-3 py-2 text-sm text-sev-critical">
      {message}
    </p>
  );
}

function ApkIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-9 w-9 text-brand"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <rect x="6" y="2.5" width="12" height="19" rx="2.5" />
      <path d="M10 18.5h4" strokeLinecap="round" />
      <path d="M12 7v6m0 0l-2.5-2.5M12 13l2.5-2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
