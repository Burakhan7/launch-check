"use client";

import { useTranslations } from "next-intl";
import { assetUrl, type ScanReport } from "@/lib/api";
import { hostOf, rebuildPrompt, seconds } from "@/lib/deep";
import { CopyButton } from "../report/CopyButton";
import { SectionHeader } from "./SeenVsSeen";

interface Example {
  origin: string;
  source: "competitor" | "reference";
  ok: boolean;
  reason: string;
  popularity: { crux?: boolean | null; majestic_rank?: number | null };
  assets: Record<string, string>;
}

interface Row {
  key: string;
  user: boolean | null;
  examples: (boolean | null)[];
  have: number;
  known: number;
  share: number | null;
}

interface BenchmarkMeta {
  status?: Record<string, string>;
  category?: string | null;
  user_assets?: Record<string, string>;
  examples?: Example[];
  matrix?: Row[];
  patterns?: { key: string; have: number; total: number }[];
  ahead?: { key: string }[];
  missing_pages?: { type: string; have: number; total: number }[];
  speed?: { method: string; user_ms: number; median_ms: number } | null;
  rebuild_sections?: string[];
}

/** Kıyas (PHASE2 §7.5): ortak desenler, fark matrisi (kart görünümü), önde olunan yerler, yan
 * yana ilk ekranlar ve "landing'ini yeniden kur" prompt'u. Örnek sitelerin metni gösterilmez. */
export function BenchmarkSection({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.benchmark");
  const tp = useTranslations("Deep.benchmark.pattern");
  const ts = useTranslations("Deep.benchmark.section");
  const data = (report.meta.benchmark ?? {}) as BenchmarkMeta;
  if (!data.status) return null;
  const examples = data.examples ?? [];
  const valid = examples.filter((e) => e.ok);
  const skipped = examples.filter((e) => !e.ok);
  const preview = (report.meta.preview ?? {}) as { title?: string };

  if (data.status.compare !== "ok") {
    return (
      <section className="flex flex-col gap-3">
        <SectionHeader title={t("title")} />
        <p className="rounded-2xl border border-line bg-surface p-4 text-sm">{t("notEnough")}</p>
        <Skipped examples={skipped} />
      </section>
    );
  }
  const prompt = rebuildPrompt(
    data.rebuild_sections ?? [],
    { target: report.target_label, title: preview.title ?? "" },
    (key, values) => t(key, values),
    (key) => ts(key),
  );
  return (
    <section className="flex flex-col gap-5">
      <SectionHeader title={t("title")} hint={t("hint", { count: valid.length })} />
      <ul className="flex flex-wrap gap-2 text-xs">
        {valid.map((e) => (
          <li key={e.origin} className="rounded-full border border-line px-3 py-1">
            {hostOf(e.origin)} · {t(`source.${e.source}`)}
            {e.popularity?.crux === false && <> · {t("popularityUnknown")}</>}
          </li>
        ))}
      </ul>
      <Skipped examples={skipped} />

      {(data.patterns ?? []).length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="font-semibold">{t("patternsTitle")}</h3>
          <ul className="flex flex-col gap-1.5 text-sm">
            {data.patterns!.map((p) => (
              <li key={p.key}>
                {t("patternLine", { pattern: tp(p.key), have: p.have, total: p.total })}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(data.ahead ?? []).length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="font-semibold">{t("aheadTitle")}</h3>
          <ul className="flex flex-wrap gap-2 text-sm">
            {data.ahead!.map((a) => (
              <li
                key={a.key}
                className="rounded-full bg-[color-mix(in_srgb,var(--score-good)_15%,transparent)] px-3 py-1 text-score-good"
              >
                {tp.has(a.key) ? tp(a.key) : t(`aheadKey.${a.key}`)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.speed && (
        <p className="text-sm">
          {t("speed", {
            user: seconds(data.speed.user_ms),
            median: seconds(data.speed.median_ms),
            method: t(`method.${data.speed.method}`),
          })}
        </p>
      )}

      <details className="rounded-2xl border border-line bg-surface">
        <summary className="cursor-pointer px-4 py-3 font-semibold">{t("matrixTitle")}</summary>
        {/* Mobilde yatay kaydırmasız: her desen bir kart */}
        <ul className="grid gap-2 border-t border-line p-4 sm:grid-cols-2">
          {(data.matrix ?? []).map((row) => (
            <li
              key={row.key}
              className="flex flex-col gap-1 rounded-xl border border-line p-3 text-sm"
            >
              <span className="font-medium">{tp(row.key)}</span>
              <span className="text-xs text-muted">
                {t("matrixCell", {
                  you: t(row.user === null ? "unknown" : row.user ? "yes" : "no"),
                  have: row.have,
                  known: row.known,
                })}
              </span>
            </li>
          ))}
        </ul>
      </details>

      <SideBySide report={report} data={data} examples={valid} />

      <div className="flex flex-col gap-3 rounded-xl bg-brand-soft p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="font-semibold text-brand">{t("rebuildTitle")}</h3>
            <p className="text-xs text-muted">{t("rebuildHint")}</p>
          </div>
          <CopyButton text={prompt} label={t("copyRebuild")} copiedLabel={t("copied")} />
        </div>
        <pre className="max-h-64 overflow-auto rounded-lg bg-background p-3 font-mono text-xs whitespace-pre-wrap">
          {prompt}
        </pre>
      </div>
    </section>
  );
}

function Skipped({ examples }: { examples: Example[] }) {
  const t = useTranslations("Deep.benchmark");
  if (examples.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1 text-xs text-muted">
      {examples.map((e) => (
        <li key={e.origin}>
          {t("skipped", {
            host: hostOf(e.origin),
            reason: t(`reason.${e.reason || "unreachable"}`),
          })}
        </li>
      ))}
    </ul>
  );
}

function SideBySide({
  report,
  data,
  examples,
}: {
  report: ScanReport;
  data: BenchmarkMeta;
  examples: Example[];
}) {
  const t = useTranslations("Deep.benchmark");
  const shown = [
    { label: t("you"), id: data.user_assets?.mobile },
    ...examples
      .filter((e) => e.assets.mobile)
      .slice(0, 3)
      .map((e) => ({ label: hostOf(e.origin), id: e.assets.mobile })),
  ].filter((s) => s.id);
  if (shown.length < 2) return null;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-semibold">{t("sideBySide")}</h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {shown.map((s) => (
          <figure key={s.id} className="flex flex-col gap-1">
            {/* eslint-disable-next-line @next/next/no-img-element -- API'den gelen WebP */}
            <img
              src={assetUrl(report.scan_id, s.id!)}
              alt={t("shotAlt", { site: s.label })}
              loading="lazy"
              className="max-h-80 w-full rounded-lg border border-line object-cover object-top"
            />
            <figcaption className="truncate text-xs text-muted">{s.label}</figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}
