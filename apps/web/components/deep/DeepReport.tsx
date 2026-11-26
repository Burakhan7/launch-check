"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useRouter } from "@/i18n/navigation";
import { startDeepScan, type Finding, type ScanReport } from "@/lib/api";
import { buildCombinedPrompt } from "@/lib/combinedPrompt";
import { notifyDone, phase1Findings, phase2Findings, planGroups } from "@/lib/deep";
import { CONTACT_URL } from "@/lib/site";
import { CopyButton } from "../report/CopyButton";
import { FindingCard } from "../report/FindingCard";
import { HelpNote, PassedList } from "../report/ReportView";
import { CategoryBar, SCORE_COLOR, ScoreRing } from "../report/Score";
import { AnimatedCounter } from "../rare/animated-counter";
import { RailToc } from "../rare/rail-toc";
import { ScrollProgress } from "../rare/scroll-progress";
import { BenchmarkSection } from "./BenchmarkSection";
import { PlanChecklist } from "./PlanChecklist";
import { FunnelMap } from "./FunnelMap";
import { SectionHeader, SeenVsSeen } from "./SeenVsSeen";

/** Faz 2 raporu (PHASE2 §8): bölümler spec'teki sırayla. */
export function DeepReport({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep");
  const tc = useTranslations("Category");
  const ts = useTranslations("ScoreLabel");
  useDoneTitle(report);
  const newFindings = phase2Findings(report);
  const label = report.score_label ?? "not_ready";

  const sections = navSections(report, (key) => t(`nav.${key}`));

  return (
    <div className="mx-auto flex w-full max-w-6xl gap-10 px-4 py-10">
      <div className="flex min-w-0 max-w-4xl flex-1 flex-col gap-10">
        <section className="flex flex-col gap-6 rounded-3xl border border-line bg-surface p-6 sm:flex-row sm:items-center sm:p-8">
          <ScoreRing score={report.score ?? 0} label={label} />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <p className="text-xs font-semibold tracking-wide text-brand uppercase">{t("badge")}</p>
            <p className="text-2xl font-bold" style={{ color: SCORE_COLOR[label] }}>
              {ts(label)}
            </p>
            <p className="truncate font-mono text-sm text-muted">{report.target_label}</p>
          </div>
          {report.category_scores && (
            <div className="flex w-full flex-col gap-3 sm:w-56">
              {Object.entries(report.category_scores).map(([name, value]) => (
                <CategoryBar key={name} name={tc(name as Finding["category"])} score={value} />
              ))}
            </div>
          )}
        </section>

        <div id="diff" className="flex scroll-mt-24 flex-col gap-4">
          <PhaseDiff report={report} />
          <DeepActions report={report} />
        </div>
        <div id="seen" className="scroll-mt-24">
          <SeenVsSeen report={report} />
        </div>

        <section id="new" className="flex scroll-mt-24 flex-col gap-4">
          <SectionHeader title={t("newTitle", { count: newFindings.length })} hint={t("newHint")} />
          {newFindings.length === 0 ? (
            <p className="rounded-2xl border border-line bg-surface p-6 text-center">
              {t("noNew")}
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              {newFindings.map((f, i) => (
                <FindingCard
                  key={f.id ?? `${f.check_id}-${i}`}
                  finding={f}
                  defaultOpen={i === 0 && f.severity === "critical"}
                />
              ))}
            </div>
          )}
        </section>

        <div id="phase1" className="scroll-mt-24">
          <VerificationSection report={report} />
        </div>
        <div id="funnel" className="scroll-mt-24">
          <FunnelMap report={report} />
        </div>
        <div id="benchmark" className="scroll-mt-24">
          <BenchmarkSection report={report} />
        </div>
        <div id="plan" className="scroll-mt-24">
          <PriorityPlan report={report} />
        </div>
        <div id="gates" className="scroll-mt-24">
          <GatesSection report={report} />
        </div>
        <div id="rescan" className="scroll-mt-24">
          <RescanSection report={report} />
        </div>
        {report.passed.length > 0 && <PassedList report={report} />}
        <HelpNote />
      </div>
      {/* Bölüm gezinmesi: masaüstünde yan ray, telefonda alttaki ilerleme hapı (Rare UI) */}
      <aside className="hidden w-56 shrink-0 lg:block">
        <RailToc items={sections} title={t("nav.title")} className="sticky top-24" />
      </aside>
      <ScrollProgress sections={sections} buttonLabel={t("nav.title")} className="lg:hidden" />
    </div>
  );
}

/** Sadece raporda gerçekten görünen bölümler gezinmeye girer. */
function navSections(
  report: ScanReport,
  label: (key: string) => string,
): { id: string; label: string }[] {
  const meta = report.meta as Record<string, { steps?: unknown[]; status?: unknown } | undefined>;
  const s = report.sections;
  const visible: [string, boolean][] = [
    ["diff", true],
    ["seen", true],
    ["new", true],
    ["phase1", phase1Findings(report).length > 0],
    ["funnel", (meta.funnel?.steps?.length ?? 0) > 0],
    ["benchmark", !!meta.benchmark?.status],
    ["plan", !!s && Object.values(s.plan).some((ids) => ids.length > 0)],
    ["gates", !!s?.gates?.total],
    ["rescan", !!s?.rescan],
  ];
  return visible.filter(([, ok]) => ok).map(([id]) => ({ id, label: label(id) }));
}

function useDoneTitle(report: ScanReport) {
  const t = useTranslations("Deep");
  useEffect(() => {
    // Tarama izlenirken bittiyse sekme başlığı/bildirim; doğrudan açılan raporda da zararsız
    if (report.status === "done" && document.visibilityState === "hidden") {
      notifyDone(t("doneTitle"), report.target_label);
    }
  }, [report.status, report.target_label, t]);
}

function PhaseDiff({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.diff");
  const diff = report.sections?.phase_diff;
  if (!diff) return null;
  const v = diff.phase2.verification;
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-line bg-background p-5 text-sm">
      {diff.phase1.score !== null && diff.phase2.score !== null && (
        <p
          className="flex items-center gap-3 text-2xl font-bold"
          aria-label={t("scores", { before: diff.phase1.score, after: diff.phase2.score })}
        >
          <AnimatedCounter value={diff.phase1.score} aria-hidden />
          <span className="text-muted" aria-hidden>
            →
          </span>
          <AnimatedCounter value={diff.phase2.score} aria-hidden />
        </p>
      )}
      <p>
        <span className="font-semibold">{t("phase1")}</span>{" "}
        {t("phase1Line", { findings: diff.phase1.findings, score: diff.phase1.score ?? "—" })}
      </p>
      <p>
        <span className="font-semibold">{t("phase2")}</span>{" "}
        {t("phase2Line", {
          confirmed: v.confirmed ?? 0,
          refuted: v.refuted ?? 0,
          escalated: v.escalated ?? 0,
          fresh: diff.phase2.new_findings,
          subdomains: diff.phase2.forgotten_subdomains,
          gates: diff.phase2.untested_gates,
          score: diff.phase2.score ?? "—",
        })}
      </p>
      {diff.score_dropped && <p className="text-muted">{t("dropped")}</p>}
    </section>
  );
}

function VerificationSection({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep");
  const phase1 = phase1Findings(report);
  if (phase1.length === 0) return null;
  const refuted = phase1.filter((f) => f.verification === "refuted");
  const kept = phase1.filter((f) => f.verification !== "refuted");
  return (
    <section className="flex flex-col gap-4">
      <SectionHeader title={t("phase1Title")} hint={t("phase1Hint")} />
      <div className="flex flex-col gap-3">
        {kept.map((f, i) => (
          <FindingCard key={f.id ?? `${f.check_id}-p1-${i}`} finding={f} defaultOpen={false} />
        ))}
      </div>
      {refuted.length > 0 && (
        <details className="rounded-2xl border border-score-good/40 bg-surface" open>
          <summary className="cursor-pointer px-4 py-3 font-semibold">
            {t("refutedTitle", { count: refuted.length })}
          </summary>
          <div className="flex flex-col gap-3 border-t border-line p-4">
            <p className="text-sm text-muted">{t("refutedHint")}</p>
            {refuted.map((f, i) => (
              <FindingCard key={f.id ?? `${f.check_id}-r-${i}`} finding={f} defaultOpen={false} />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

const PLAN_GROUPS = ["today", "week", "month"] as const;

function PriorityPlan({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.plan");
  const tp = useTranslations("CombinedPrompt");
  const tsev = useTranslations("Severity");
  const groups = planGroups(report);
  if (PLAN_GROUPS.every((g) => groups[g].length === 0)) return null;
  return (
    <section className="flex flex-col gap-4">
      <SectionHeader title={t("title")} hint={t("hint")} />
      <div className="grid gap-4 md:grid-cols-3">
        {PLAN_GROUPS.map((group) => {
          const items = groups[group];
          const combined = buildCombinedPrompt(items, {
            target: report.target_label,
            platforms: (report.meta.platform as string[] | undefined) ?? [],
            t: (key, values) => tp(key, values),
            severityLabel: (s) => tsev(s),
          });
          return (
            <div
              key={group}
              className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4"
            >
              <h3 className="font-semibold">{t(group, { count: items.length })}</h3>
              <PlanChecklist
                scanId={report.scan_id}
                findings={items}
                minutesLabel={(count) => t("minutes", { count })}
              />
              {combined.included > 0 && (
                <CopyButton text={combined.text} label={t("copy")} copiedLabel={t("copied")} />
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function GatesSection({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.gates");
  const gates = report.sections?.gates;
  if (!gates || !gates.total) return null;
  const endpoints = gates.api_endpoints ?? [];
  const supabase = gates.supabase ?? { projects: [], tables: [], buckets: [] };
  const lists: [string, string[]][] = [
    [t("endpoints"), endpoints.map((e) => `${e.method} ${e.host}${e.path}`)],
    [t("supabaseProjects"), supabase.projects],
    [t("tables"), [...supabase.tables, ...supabase.buckets.map((b) => `${b} (storage)`)]],
    [t("firebase"), gates.firebase?.projects ?? []],
    [t("auth"), gates.auth_endpoints ?? []],
    [t("webhooks"), gates.webhooks ?? []],
    [t("forms"), gates.personal_data_forms ?? []],
    [t("admin"), gates.admin_unverified ?? []],
  ];
  return (
    <section className="flex flex-col gap-4 rounded-3xl border border-line bg-surface p-6">
      <SectionHeader title={t("title")} />
      <p className="text-sm">
        {t("summary", {
          endpoints: endpoints.length,
          projects: supabase.projects.length,
          tables: supabase.tables.length,
        })}
      </p>
      <p className="text-sm text-muted">{t("notTested")}</p>
      <div className="grid gap-4 sm:grid-cols-2">
        {lists
          .filter(([, items]) => items.length > 0)
          .map(([title, items]) => (
            <div key={title} className="flex flex-col gap-1">
              <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{title}</h3>
              <ul className="flex flex-col gap-0.5 font-mono text-xs break-all">
                {items.slice(0, 15).map((item) => (
                  <li key={item}>{item}</li>
                ))}
                {items.length > 15 && <li className="text-muted">+{items.length - 15}</li>}
              </ul>
            </div>
          ))}
      </div>
      <div className="flex flex-wrap items-center gap-3 rounded-xl bg-brand-soft p-4 text-sm">
        <span className="flex-1">{t("phase3")}</span>
        <a
          href={CONTACT_URL}
          className="rounded-xl border border-brand px-4 py-2 font-semibold text-brand"
        >
          {t("phase3Cta")}
        </a>
      </div>
    </section>
  );
}

function RescanSection({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.rescan");
  const diff = report.sections?.rescan;
  if (!diff) return null;
  const groups = [
    ["closed", diff.closed],
    ["new", diff.new],
    ["persisting", diff.persisting],
  ] as const;
  return (
    <section className="flex flex-col gap-4">
      <SectionHeader
        title={t("title")}
        hint={t("score", { before: diff.score_before ?? "—", after: diff.score_after ?? "—" })}
      />
      <div className="grid gap-4 md:grid-cols-3">
        {groups.map(([key, items]) => (
          <div
            key={key}
            className="flex flex-col gap-2 rounded-2xl border border-line bg-surface p-4"
          >
            <h3 className="font-semibold">{t(key, { count: items.length })}</h3>
            <ul className="flex flex-col gap-1 text-sm">
              {items.map((item, i) => (
                <li key={`${item.check_id}-${i}`}>{item.title}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Paylaş + "düzelttin mi? tekrar tara" (aynı ayarlarla yeni Faz 2, önceki raporla kıyaslı). */
function DeepActions({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep");
  const tr = useTranslations("Report");
  const locale = useLocale();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function rescan() {
    if (!report.parent_scan_id) return;
    setBusy(true);
    try {
      const res = await startDeepScan(
        report.parent_scan_id,
        { competitors: [], use_reference: true, compare_to: report.scan_id },
        locale,
      );
      router.push(`/report/${res.scan_id}`);
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error && err.message ? err.message : t("rescanFailed"));
    }
  }
  return (
    <div className="flex flex-wrap items-start gap-2">
      <CopyButton
        text={() => window.location.href}
        label={tr("share")}
        copiedLabel={tr("shareCopied")}
        variant="outline"
      />
      <button
        type="button"
        onClick={rescan}
        disabled={busy || !report.parent_scan_id}
        className="rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-brand-contrast hover:bg-brand-strong disabled:opacity-70"
      >
        {t("rescanButton")}
      </button>
      {error && <p className="w-full text-sm text-sev-critical">{error}</p>}
    </div>
  );
}
