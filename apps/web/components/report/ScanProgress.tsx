"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import type { ScanStatus, TargetType } from "@/lib/api";

const WEB_STEPS = ["fetch", "secrets", "files", "headers", "report"] as const;
const APK_STEPS = ["unpack", "manifest", "secrets", "build", "report"] as const;
// API gerçek adım bilgisi vermiyor; adımlar tipik süreye göre ilerler, son adım tarama bitene
// kadar bekler (Faz 2'de worker'dan gerçek ilerleme alınabilir).
const EXPECTED_MS: Record<TargetType, number> = { web: 35_000, apk: 60_000 };
const TICK_MS = 500;

export function ScanProgress({
  targetType,
  status,
}: {
  targetType: TargetType;
  status: ScanStatus;
}) {
  const t = useTranslations("Progress");
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setElapsed(Date.now() - start), TICK_MS);
    return () => clearInterval(id);
  }, []);

  const steps = targetType === "apk" ? APK_STEPS : WEB_STEPS;
  const group = targetType === "apk" ? "apkSteps" : "webSteps";
  const perStep = EXPECTED_MS[targetType] / steps.length;
  const current =
    status === "queued" ? -1 : Math.min(steps.length - 1, Math.floor(elapsed / perStep));

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col items-center gap-6 px-4 py-16 text-center">
      <Spinner />
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted">{t("subtitle")}</p>
      </div>
      <ol className="flex w-full flex-col gap-2 text-left">
        {status === "queued" && <StepRow label={t("queued")} state="active" />}
        {steps.map((step, i) => (
          <StepRow
            key={step}
            label={t(`${group}.${step}`)}
            state={i < current ? "done" : i === current ? "active" : "todo"}
          />
        ))}
      </ol>
    </div>
  );
}

function StepRow({ label, state }: { label: string; state: "done" | "active" | "todo" }) {
  const dot =
    state === "done"
      ? "bg-brand text-brand-contrast"
      : state === "active"
        ? "border-2 border-brand animate-pulse"
        : "border-2 border-line";
  return (
    <li
      className={`flex items-center gap-3 rounded-xl px-4 py-3 ${state === "active" ? "bg-brand-soft" : ""} ${state === "todo" ? "text-muted" : ""}`}
    >
      <span
        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] ${dot}`}
      >
        {state === "done" ? "✓" : ""}
      </span>
      <span className="text-sm">{label}</span>
    </li>
  );
}

function Spinner() {
  return (
    <div className="relative h-16 w-16" aria-hidden="true">
      <div className="absolute inset-0 rounded-full border-4 border-brand-soft" />
      <div className="absolute inset-0 animate-spin rounded-full border-4 border-transparent border-t-brand" />
    </div>
  );
}
