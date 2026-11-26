"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useRouter } from "@/i18n/navigation";
import { ApiError, getCategories, startDeepScan, type ScanReport } from "@/lib/api";
import { MAX_COMPETITORS } from "@/lib/deep";

/** Faz 1 raporunun üstünde: derin taramayı (Faz 2) başlatma paneli (PHASE2 §3.1, §7.1). */
export function StartDeepScan({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.start");
  const te = useTranslations("Deep.errors");
  const locale = useLocale();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [competitors, setCompetitors] = useState<string[]>([""]);
  const [useReference, setUseReference] = useState(true);
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<{ key: string; label: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || categories.length) return;
    getCategories(locale)
      .then((res) => setCategories(res.categories))
      .catch(() => setCategories([]));
  }, [open, locale, categories.length]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await startDeepScan(
        report.scan_id,
        {
          competitors: competitors.map((c) => c.trim()).filter(Boolean),
          use_reference: useReference,
          category: category || null,
        },
        locale,
      );
      router.push(`/report/${res.scan_id}`);
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "internal_error";
      setError(
        err instanceof ApiError && err.message
          ? err.message
          : te.has(code)
            ? te(code)
            : te("internal_error"),
      );
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <section className="flex flex-col gap-3 rounded-3xl border border-brand/30 bg-brand-soft p-6 sm:flex-row sm:items-center">
        <div className="flex flex-1 flex-col gap-1">
          <h2 className="text-lg font-semibold text-brand">{t("title")}</h2>
          <p className="text-sm text-muted">{t("intro")}</p>
        </div>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-brand-contrast hover:bg-brand-strong"
        >
          {t("open")}
        </button>
      </section>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-5 rounded-3xl border border-brand/30 bg-surface p-6"
    >
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted">{t("details")}</p>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">{t("competitors")}</legend>
        <p className="text-xs text-muted">{t("competitorsHint")}</p>
        {competitors.map((value, i) => (
          <input
            key={i}
            type="url"
            inputMode="url"
            autoComplete="off"
            value={value}
            placeholder={t("competitorPlaceholder")}
            aria-label={t("competitorLabel", { n: i + 1 })}
            onChange={(e) =>
              setCompetitors(competitors.map((c, j) => (j === i ? e.target.value : c)))
            }
            className="rounded-xl border border-line bg-background px-3 py-2 text-sm"
          />
        ))}
        {competitors.length < MAX_COMPETITORS && (
          <button
            type="button"
            onClick={() => setCompetitors([...competitors, ""])}
            className="self-start text-sm font-medium text-brand hover:underline"
          >
            {t("addCompetitor")}
          </button>
        )}
      </fieldset>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">{t("category")}</span>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-xl border border-line bg-background px-3 py-2"
        >
          <option value="">{t("categoryAuto")}</option>
          {categories.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={useReference}
          onChange={(e) => setUseReference(e.target.checked)}
          className="mt-1"
        />
        <span>{t("useReference")}</span>
      </label>
      <p className="text-xs text-muted">{t("passiveNote")}</p>
      {error && (
        <p
          role="alert"
          className="rounded-lg bg-sev-critical-soft px-3 py-2 text-sm text-sev-critical"
        >
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-brand-contrast hover:bg-brand-strong disabled:opacity-70"
        >
          {busy ? t("starting") : t("submit")}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-xl border border-line px-4 py-2 text-sm"
        >
          {t("cancel")}
        </button>
      </div>
    </form>
  );
}
