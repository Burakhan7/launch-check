"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import type { Finding, Impact, Severity, Verification } from "@/lib/api";
import { evidenceRows } from "@/lib/evidence";
import { CopyButton } from "./CopyButton";

export const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-sev-critical-soft text-sev-critical",
  high: "bg-sev-high-soft text-sev-high",
  medium: "bg-sev-medium-soft text-sev-medium",
  low: "bg-sev-low-soft text-sev-low",
  info: "bg-sev-info-soft text-sev-info",
};

const SEVERITY_BAR: Record<Severity, string> = {
  critical: "bg-sev-critical",
  high: "bg-sev-high",
  medium: "bg-sev-medium",
  low: "bg-sev-low",
  info: "bg-sev-info",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const t = useTranslations("Severity");
  return (
    <span
      className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${SEVERITY_STYLE[severity]}`}
    >
      {t(severity)}
    </span>
  );
}

export function FindingCard({ finding, defaultOpen }: { finding: Finding; defaultOpen: boolean }) {
  const t = useTranslations("Report");
  const tc = useTranslations("Category");
  return (
    <details
      open={defaultOpen}
      className="group relative overflow-hidden rounded-2xl border border-line bg-background open:shadow-[0_10px_40px_-20px_color-mix(in_srgb,var(--brand)_40%,transparent)]"
    >
      <span
        className={`absolute inset-y-0 left-0 w-1 ${SEVERITY_BAR[finding.severity]}`}
        aria-hidden="true"
      />
      <summary className="flex cursor-pointer list-none items-start gap-3 px-5 py-4 [&::-webkit-details-marker]:hidden">
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={finding.severity} />
            <span className="text-xs text-muted">{tc(finding.category)}</span>
            {finding.verification && finding.verification !== "not_verifiable" && (
              <VerificationBadge value={finding.verification} />
            )}
          </div>
          <h3 className="font-semibold text-pretty">{finding.title}</h3>
        </div>
        <Chevron />
      </summary>

      <div className="flex flex-col gap-5 border-t border-line px-5 py-5 text-sm leading-relaxed">
        {finding.evidence.sensitive_data_note === true && (
          <p className="rounded-lg border border-brand/30 bg-brand-soft px-3 py-2 text-sm font-medium text-brand">
            {finding.evidence.escalated_due_to ? t("sensitiveEscalated") : t("sensitiveNote")}
          </p>
        )}
        {finding.impact && <ImpactBlock impact={finding.impact} />}
        <Section title={t("description")}>
          <p>{finding.description}</p>
        </Section>
        <Section title={t("why")}>
          <p>{finding.why_it_matters}</p>
        </Section>
        {finding.fix_steps.length > 0 && (
          <Section title={t("fixSteps")}>
            <ol className="flex list-decimal flex-col gap-1.5 pl-5 marker:font-semibold marker:text-brand">
              {finding.fix_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </Section>
        )}
        {finding.fix_prompt && (
          <div className="flex flex-col gap-3 rounded-xl bg-brand-soft p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h4 className="font-semibold text-brand">{t("fixPrompt")}</h4>
                <p className="text-xs text-muted">{t("fixPromptHint")}</p>
              </div>
              <CopyButton
                text={finding.fix_prompt}
                label={t("copyPrompt")}
                copiedLabel={t("copied")}
              />
            </div>
            <pre className="max-h-64 overflow-auto rounded-lg bg-background p-3 font-mono text-xs whitespace-pre-wrap">
              {finding.fix_prompt}
            </pre>
          </div>
        )}
        <EvidenceBlock evidence={finding.evidence} />
      </div>
    </details>
  );
}

const VERIFICATION_STYLE: Record<Verification, string> = {
  confirmed: "border-sev-high/40 text-sev-high",
  escalated: "border-sev-critical/40 text-sev-critical",
  refuted: "border-score-good/40 text-score-good",
  not_verifiable: "border-line text-muted",
};

export function VerificationBadge({ value }: { value: Verification }) {
  const t = useTranslations("Deep.verification");
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${VERIFICATION_STYLE[value]}`}
    >
      {t(value)}
    </span>
  );
}

/** Etki kartı (PHASE2 §8.4): kim, ne kadar kolay, ne olur, yasal boyut, zaman çizelgesi. */
function ImpactBlock({ impact }: { impact: Impact }) {
  const t = useTranslations("Deep.impact");
  const rows: [string, string][] = [
    [t("who"), impact.who],
    [t("ease"), t(`easeValue.${impact.ease}`)],
    [t("consequence"), impact.consequence],
  ];
  if (impact.legal) rows.push([t("legal"), impact.legal]);
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-semibold tracking-wide text-muted uppercase">{t("title")}</h4>
        <MeasureTag />
      </div>
      <dl className="flex flex-col gap-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
            <dt className="shrink-0 text-xs font-medium text-muted sm:w-32">{label}</dt>
            <dd className="min-w-0 flex-1">{value}</dd>
          </div>
        ))}
        <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
          <dt className="shrink-0 text-xs font-medium text-muted sm:w-32">{t("timeline")}</dt>
          <dd className="min-w-0 flex-1">
            <ul className="flex flex-col gap-1">
              {impact.timeline.map((item) => (
                <li key={item.when}>
                  <span className="font-medium">{t(`when.${item.when}`)}:</span> {item.text}
                </li>
              ))}
            </ul>
          </dd>
        </div>
        <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
          <dt className="shrink-0 text-xs font-medium text-muted sm:w-32">{t("fixTime")}</dt>
          <dd>{t("minutes", { count: impact.fix_minutes })}</dd>
        </div>
      </dl>
    </div>
  );
}

/** Deterministik ölçüm etiketi; LLM yorumları "Yorum" etiketi taşır (PHASE2 §1.2-4). */
export function MeasureTag({ kind = "measure" }: { kind?: "measure" | "comment" }) {
  const t = useTranslations("Deep");
  return (
    <span className="rounded-md bg-surface-strong px-2 py-0.5 text-[11px] font-semibold text-muted">
      {t(kind === "measure" ? "measureTag" : "commentTag")}
    </span>
  );
}

function EvidenceBlock({ evidence }: { evidence: Record<string, unknown> }) {
  const t = useTranslations("Evidence");
  const tr = useTranslations("Report");
  const rows = evidenceRows(evidence, (_key, value) =>
    t.has(`values.${value}`) ? t(`values.${value}`) : value,
  );
  if (rows.length === 0) return null;
  return (
    <Section title={tr("evidence")}>
      <dl className="flex flex-col gap-2 rounded-xl border border-line bg-surface p-3">
        {rows.map((row) => (
          <div key={row.key} className="flex flex-col gap-1 sm:flex-row sm:gap-3">
            <dt className="shrink-0 text-xs font-medium text-muted sm:w-36">
              {t.has(`keys.${row.key}`) ? t(`keys.${row.key}`) : row.key}
            </dt>
            <dd className="min-w-0 flex-1 text-xs">
              {row.value.kind === "list" ? (
                <ul className="flex flex-col gap-0.5 font-mono break-all">
                  {row.value.items.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                  {row.value.more > 0 && <li className="text-muted">+{row.value.more}</li>}
                </ul>
              ) : (
                <span className={row.value.kind === "code" ? "font-mono break-all" : ""}>
                  {row.value.value}
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {"masked" in evidence && <p className="text-xs text-muted">{tr("evidenceMasked")}</p>}
    </Section>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className="text-xs font-semibold tracking-wide text-muted uppercase">{title}</h4>
      {children}
    </section>
  );
}

function Chevron() {
  return (
    <svg
      viewBox="0 0 20 20"
      className="mt-1 h-5 w-5 shrink-0 text-muted transition group-open:rotate-180"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <path d="M5 8l5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
