"use client";

import { useTranslations } from "next-intl";
import { assetUrl, type ScanReport } from "@/lib/api";
import { seconds } from "@/lib/deep";
import { SectionHeader } from "./SeenVsSeen";

interface FunnelStep {
  kind: string;
  url: string;
  clicks: number;
  load_ms: number | null;
  asset_id: string | null;
  issues: string[];
}

interface FunnelMeta {
  steps?: FunnelStep[];
  signup?: { fields: number; required: number; social: string[]; card: boolean } | null;
  price_distance?: number | null;
}

/** Huni haritası (PHASE2 §8.6): telefonda ana sayfadan kayda ve fiyata giden yol. */
export function FunnelMap({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.funnel");
  const titles = new Map(report.findings.map((f) => [f.check_id, f.title]));
  const funnel = (report.meta.funnel ?? {}) as FunnelMeta;
  const steps = funnel.steps ?? [];
  if (steps.length === 0) return null;
  return (
    <section className="flex flex-col gap-4">
      <SectionHeader title={t("title")} hint={t("hint")} />
      <ol className="flex flex-col gap-3 sm:flex-row sm:overflow-x-auto">
        {steps.map((step, i) => (
          <li
            key={`${step.kind}-${i}`}
            className="flex min-w-0 flex-col gap-2 rounded-2xl border border-line bg-surface p-3 sm:w-56 sm:shrink-0"
          >
            {step.asset_id && (
              // eslint-disable-next-line @next/next/no-img-element -- API'den gelen WebP
              <img
                src={assetUrl(report.scan_id, step.asset_id)}
                alt={t("shotAlt", { step: t(`kind.${step.kind}`) })}
                loading="lazy"
                className="max-h-64 w-full rounded-lg border border-line object-cover object-top"
              />
            )}
            <span className="font-medium">
              {i + 1}. {t(`kind.${step.kind}`)}
            </span>
            <span className="font-mono text-xs break-all text-muted">{step.url}</span>
            <span className="text-xs text-muted">
              {t("meta", { seconds: seconds(step.load_ms), clicks: step.clicks })}
            </span>
            {step.issues.length > 0 && (
              <ul className="flex flex-col gap-1 text-xs text-sev-high">
                {step.issues.map((id) => (
                  <li key={id}>• {titles.get(id) ?? id}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      {funnel.signup && (
        <p className="text-sm text-muted">
          {t("signupSummary", {
            fields: funnel.signup.fields,
            required: funnel.signup.required,
            social: funnel.signup.social.length ? funnel.signup.social.join(", ") : t("none"),
          })}
        </p>
      )}
    </section>
  );
}
