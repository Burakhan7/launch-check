"use client";

import { useFormatter, useLocale, useTranslations } from "next-intl";
import { useParams } from "next/navigation";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, getScan, startWebScan, type Category, type ScanReport } from "@/lib/api";
import { buildCombinedPrompt } from "@/lib/combinedPrompt";
import { canStartDeep } from "@/lib/deep";
import { CONTACT_URL, POLL_INTERVAL_MS } from "@/lib/site";
import { StartDeepScan } from "../deep/StartDeepScan";

// Faz 2 görünümleri (animasyonlu Rare UI bileşenleri dahil) sadece Faz 2 raporunda yüklenir
const DeepProgress = dynamic(() => import("../deep/DeepProgress").then((m) => m.DeepProgress));
const DeepReport = dynamic(() => import("../deep/DeepReport").then((m) => m.DeepReport));
import { CopyButton } from "./CopyButton";
import { FindingCard } from "./FindingCard";
import { ScanProgress } from "./ScanProgress";
import { CategoryBar, SCORE_COLOR, ScoreRing } from "./Score";

type LoadState =
  | { kind: "loading" }
  | { kind: "not_found" }
  | { kind: "error"; message: string }
  | { kind: "report"; report: ScanReport };

const FINISHED = new Set(["done", "failed"]);

function useScan(id: string, locale: string): LoadState {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const t = useTranslations("Errors");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const report = await getScan(id, locale);
        if (cancelled) return;
        setState({ kind: "report", report });
        if (!FINISHED.has(report.status)) timer = setTimeout(poll, POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) setState({ kind: "not_found" });
        else if (err instanceof ApiError && err.code === "network_error") {
          // Geçici ağ hatası: sessizce tekrar dene
          timer = setTimeout(poll, POLL_INTERVAL_MS * 2);
        } else setState({ kind: "error", message: t("generic") });
      }
    }
    poll();
    // cacheComponents: sayfa gizlendiğinde effect temizlenir, polling durur
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id, locale, t]);

  return state;
}

export function ReportView() {
  const { id } = useParams<{ id: string }>();
  const locale = useLocale();
  const state = useScan(id, locale);
  const t = useTranslations("Report");

  if (state.kind === "loading") {
    return <p className="py-24 text-center text-muted">{t("loading")}</p>;
  }
  if (state.kind === "not_found") {
    return <Notice title={t("notFoundTitle")} text={t("notFoundText")} />;
  }
  if (state.kind === "error") {
    return <Notice title={t("failedTitle")} text={state.message} />;
  }
  const { report } = state;
  if (report.status === "queued" || report.status === "running") {
    if (report.phase === 2) return <DeepProgress report={report} />;
    return <ScanProgress targetType={report.target_type} status={report.status} />;
  }
  if (report.status === "failed") {
    return <Notice title={t("failedTitle")} text={report.error?.message ?? ""} report={report} />;
  }
  if (report.phase === 2) return <DeepReport report={report} />;
  return <Report report={report} />;
}

function Report({ report }: { report: ScanReport }) {
  const t = useTranslations("Report");
  const tc = useTranslations("Category");
  // Satışı/büyümeyi etkileyen (conversion) bulgular ayrı bölümde gösterilir
  const coreFindings = report.findings.filter((f) => f.category !== "conversion");
  const conversionFindings = report.findings.filter((f) => f.category === "conversion");
  const ts = useTranslations("ScoreLabel");
  const format = useFormatter();
  const score = report.score ?? 0;
  const label = report.score_label ?? "not_ready";
  const dateOpts = { dateStyle: "medium", timeStyle: "short" } as const;

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-4 py-10">
      <section className="flex flex-col gap-6 rounded-3xl border border-line bg-surface p-6 sm:flex-row sm:items-center sm:p-8">
        <ScoreRing score={score} label={label} />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <p className="text-2xl font-bold" style={{ color: SCORE_COLOR[label] }}>
            {ts(label)}
          </p>
          <p className="truncate font-mono text-sm text-muted" title={report.target_label}>
            {report.target_label}
          </p>
          <TargetBadges report={report} />
          <p className="text-xs text-muted">
            {t("scannedAt", {
              date: format.dateTime(new Date(report.finished_at ?? report.created_at), dateOpts),
            })}
            {" · "}
            {t("expires", {
              date: format.dateTime(new Date(report.expires_at), { dateStyle: "medium" }),
            })}
          </p>
        </div>
        {report.category_scores && (
          <div className="flex w-full flex-col gap-3 sm:w-56">
            <h2 className="text-xs font-semibold tracking-wide text-muted uppercase">
              {t("categories")}
            </h2>
            {Object.entries(report.category_scores).map(([name, value]) => (
              <CategoryBar key={name} name={tc(name as Category)} score={value} />
            ))}
          </div>
        )}
      </section>

      <Actions report={report} />
      {canStartDeep(report) && <StartDeepScan report={report} />}

      <section className="flex flex-col gap-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xl font-semibold">{t("findingsTitle")}</h2>
          <span className="text-sm text-muted">
            {t("findingsCount", { count: report.findings.length })}
          </span>
        </div>
        {report.findings.length === 0 ? (
          <p className="rounded-2xl border border-line bg-surface p-6 text-center">
            {t("noFindings")}
          </p>
        ) : (
          coreFindings.length > 0 && (
            <div className="flex flex-col gap-3">
              {coreFindings.map((finding, i) => (
                <FindingCard
                  key={`${finding.check_id}-${i}`}
                  finding={finding}
                  defaultOpen={i === 0 && finding.severity === "critical"}
                />
              ))}
            </div>
          )
        )}
      </section>

      {conversionFindings.length > 0 && (
        <section className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-xl font-semibold">{t("conversionTitle")}</h2>
            <p className="text-sm text-muted">{t("conversionHint")}</p>
          </div>
          <div className="flex flex-col gap-3">
            {conversionFindings.map((finding, i) => (
              <FindingCard
                key={`${finding.check_id}-c${i}`}
                finding={finding}
                defaultOpen={false}
              />
            ))}
          </div>
        </section>
      )}

      {report.passed.length > 0 && <PassedList report={report} />}

      <p className="text-center text-xs text-muted">{t("phaseNote")}</p>
      <HelpNote />
    </div>
  );
}

// Web taramasında tespit edilen içerik türü bayrakları (meta.<bayrak> === true ise rozet)
const CONTENT_FLAGS = [
  "is_ugc",
  "has_live_media",
  "is_emotional_support",
  "has_anonymous_posting",
  "has_media_upload",
  "collects_special_category_data",
  "is_dating_or_matching",
  "has_login",
] as const;

function TargetBadges({ report }: { report: ScanReport }) {
  const t = useTranslations("Report");
  const tp = useTranslations("Platform");
  const tf = useTranslations("ContentFlag");
  const meta = report.meta;
  const flags = CONTENT_FLAGS.filter((flag) => meta[flag] === true);
  const specialCategories = (meta.special_categories as string[] | undefined) ?? [];
  const names: string[] =
    report.target_type === "apk"
      ? [String(meta.framework ?? ""), meta.hermes ? "hermes" : ""].filter(Boolean)
      : ((meta.platform as string[] | undefined) ?? []);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {names.length + flags.length > 0 && (
        <span className="text-xs text-muted">{t("detected")}:</span>
      )}
      {names.map((name) => (
        <span
          key={name}
          className="rounded-full bg-brand-soft px-2.5 py-0.5 text-xs font-semibold text-brand"
        >
          {tp.has(name) ? tp(name) : name}
        </span>
      ))}
      {flags.map((flag) => (
        <span
          key={flag}
          className="rounded-full border border-brand/30 px-2.5 py-0.5 text-xs font-medium text-brand"
        >
          {tf(flag)}
          {flag === "collects_special_category_data" && specialCategories.length > 0 && (
            <>
              :{" "}
              {specialCategories
                .map((c) => (tf.has(`special.${c}`) ? tf(`special.${c}`) : c))
                .join(", ")}
            </>
          )}
        </span>
      ))}
      {report.target_type === "apk" && meta.version_name !== undefined && (
        <span className="text-xs text-muted">
          {t("apkInfo", {
            version: String(meta.version_name || "?"),
            target: String(meta.target_sdk ?? "?"),
          })}
        </span>
      )}
    </div>
  );
}

function reportPlatforms(report: ScanReport): string[] {
  const meta = report.meta;
  if (report.target_type === "apk") return [String(meta.framework ?? "")].filter(Boolean);
  return (meta.platform as string[] | undefined) ?? [];
}

function Actions({ report }: { report: ScanReport }) {
  const t = useTranslations("Report");
  const tp = useTranslations("CombinedPrompt");
  const tsev = useTranslations("Severity");
  const combined = buildCombinedPrompt(report.findings, {
    target: report.target_label,
    platforms: reportPlatforms(report),
    t: (key, values) => tp(key, values),
    severityLabel: (severity) => tsev(severity),
  });
  return (
    <div className="flex flex-wrap items-start gap-2">
      <RescanButton report={report} />
      <CopyButton
        text={() => window.location.href}
        label={t("share")}
        copiedLabel={t("shareCopied")}
        variant="outline"
      />
      {combined.included > 0 && (
        <div className="flex flex-col gap-1">
          <CopyButton
            text={combined.text}
            label={t("copyAllPrompts", { count: combined.included })}
            copiedLabel={t("copied")}
          />
          <p className="max-w-xs text-xs text-muted">
            {t("copyAllHint")}
            {combined.omitted > 0 && <> {t("copyAllOmitted", { count: combined.omitted })}</>}
          </p>
        </div>
      )}
    </div>
  );
}

function RescanButton({ report }: { report: ScanReport }) {
  const t = useTranslations("Report");
  const locale = useLocale();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const style =
    "inline-flex items-center rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-brand-contrast transition hover:bg-brand-strong disabled:opacity-70";

  if (report.target_type === "apk" || !report.target_label) {
    return (
      <Link href="/#apk" className={style}>
        {t("rescanApk")}
      </Link>
    );
  }
  async function rescan() {
    setBusy(true);
    try {
      const { scan_id } = await startWebScan(report.target_label, locale);
      router.push(`/report/${scan_id}`);
    } catch {
      setBusy(false);
      router.push("/");
    }
  }
  return (
    <button type="button" onClick={rescan} disabled={busy} className={style}>
      {t("rescan")}
    </button>
  );
}

export function PassedList({ report }: { report: ScanReport }) {
  const t = useTranslations("Report");
  const tc = useTranslations("Category");
  const groups = new Map<Category, string[]>();
  for (const item of report.passed) {
    groups.set(item.category, [...(groups.get(item.category) ?? []), item.title]);
  }
  return (
    <details className="group rounded-2xl border border-line bg-surface">
      <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-4 [&::-webkit-details-marker]:hidden">
        <span className="font-semibold">{t("passedTitle")}</span>
        <span className="rounded-full bg-[color-mix(in_srgb,var(--score-good)_15%,transparent)] px-3 py-1 text-xs font-semibold text-score-good">
          {t("passedCount", { count: report.passed.length })}
        </span>
      </summary>
      <div className="grid gap-5 border-t border-line px-5 py-5 sm:grid-cols-2">
        {[...groups.entries()].map(([category, titles]) => (
          <div key={category} className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">
              {tc(category)}
            </h3>
            <ul className="flex flex-col gap-1.5 text-sm">
              {titles.map((title) => (
                <li key={title} className="flex gap-2">
                  <span className="text-score-good" aria-hidden="true">
                    ✓
                  </span>
                  <span>{title}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </details>
  );
}

export function HelpNote() {
  const t = useTranslations("Report");
  return (
    <p className="text-center text-sm text-muted">
      <span className="font-medium text-foreground">{t("helpTitle")}</span> {t("helpText")}{" "}
      <a href={CONTACT_URL} className="font-medium text-brand underline-offset-4 hover:underline">
        {t("helpLink")}
      </a>
    </p>
  );
}

function Notice({ title, text, report }: { title: string; text: string; report?: ScanReport }) {
  const t = useTranslations("Report");
  return (
    <div className="mx-auto flex w-full max-w-lg flex-col items-center gap-4 px-4 py-24 text-center">
      <h1 className="text-2xl font-bold">{title}</h1>
      <p className="text-muted">{text}</p>
      <div className="flex flex-wrap justify-center gap-2">
        {report ? (
          <RescanButton report={report} />
        ) : (
          <Link
            href="/"
            className="rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-brand-contrast hover:bg-brand-strong"
          >
            {t("newScan")}
          </Link>
        )}
      </div>
    </div>
  );
}
