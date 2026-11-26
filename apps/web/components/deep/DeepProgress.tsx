"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { getEvents, type DeepEvent, type ModuleStatus, type ScanReport } from "@/lib/api";
import { DEEP_MODULES } from "@/lib/deep";
import { POLL_INTERVAL_MS } from "@/lib/site";
import { AiStatus } from "../rare/ai-status";
import FluidOrb from "../rare/fluid-orb";
import { FindingCard } from "../report/FindingCard";

const MAX_EVENTS = 12;

/** Faz 2 sürerken (PHASE2 §8.2): modül kartları, canlı olay akışı, sıra/ETA, erken bulgular.
 * Bekleme yapay değildir: ilerleme sunucudaki gerçek modül durumlarından gelir. */
export function DeepProgress({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.progress");
  const events = useEvents(report.scan_id, report.status);
  const modules = report.progress?.modules ?? {};
  const notifications = useNotificationOptIn();

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-10">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted">{t("subtitle")}</p>
        <p className="font-mono text-sm text-muted">{report.target_label}</p>
      </header>

      {report.status === "queued" && (
        <FluidOrb size={120} color="#7c3aed" className="self-center" aria-hidden />
      )}
      {report.status === "queued" && (
        <p className="rounded-2xl border border-line bg-surface p-4" role="status">
          {t("queued", {
            position: report.queue_position ?? 1,
            minutes: Math.max(1, Math.round((report.eta_s ?? 0) / 60)),
          })}
        </p>
      )}
      {report.status === "running" && report.eta_s ? (
        <p className="text-sm text-muted" role="status">
          {t("eta", { minutes: Math.max(1, Math.round(report.eta_s / 60)) })}
        </p>
      ) : null}

      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {DEEP_MODULES.map((name) => (
          <ModuleCard key={name} name={name} info={modules[name]} />
        ))}
      </ul>

      {events.length > 0 && (
        <section className="flex flex-col gap-2" aria-live="polite">
          <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">{t("feed")}</h2>
          <ol className="flex flex-col gap-1 rounded-2xl border border-line bg-surface p-4 text-sm">
            {events.slice(-MAX_EVENTS).map((e) => (
              <li key={e.id}>{e.text}</li>
            ))}
          </ol>
        </section>
      )}

      {notifications.supported && notifications.state === "default" && (
        <button
          type="button"
          onClick={notifications.request}
          className="self-start rounded-xl border border-line px-4 py-2 text-sm hover:border-brand hover:text-brand"
        >
          {t("notify")}
        </button>
      )}

      {report.findings.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">{t("early", { count: report.findings.length })}</h2>
          {report.findings.map((f, i) => (
            <FindingCard key={f.id ?? `${f.check_id}-${i}`} finding={f} defaultOpen={false} />
          ))}
        </section>
      )}
    </div>
  );
}

// Modül durumu → Rare UI AI Status durumu (bekleyen: etkin olmayan özel durum)
const AI_STATE: Record<ModuleStatus, string> = {
  pending: "waiting",
  running: "analyzing",
  done: "done",
  partial: "done",
  failed: "error",
};

function ModuleCard({
  name,
  info,
}: {
  name: string;
  info?: { status: ModuleStatus; found: number };
}) {
  const t = useTranslations("Deep");
  const status = info?.status ?? "pending";
  const finished = status !== "pending" && status !== "running";
  const detail = finished
    ? `${t(`moduleStatus.${status}`)} · ${t("found", { count: info?.found ?? 0 })}`
    : t(`moduleStatus.${status}`);
  return (
    <li
      className={`rounded-2xl border p-3 ${status === "running" ? "border-brand bg-brand-soft" : "border-line"}`}
    >
      <AiStatus
        state={AI_STATE[status]}
        states={{ waiting: { active: false } }}
        label={t(`modules.${name}`)}
        detail={detail}
        timer={false}
      />
    </li>
  );
}

function useEvents(scanId: string, status: string): DeepEvent[] {
  const locale = useLocale();
  const [events, setEvents] = useState<DeepEvent[]>([]);
  const last = useRef(0);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const res = await getEvents(scanId, last.current, locale);
        if (cancelled) return;
        if (res.events.length) {
          last.current = res.events[res.events.length - 1].id;
          setEvents((prev) => [...prev, ...res.events].slice(-50));
        }
      } catch {
        // geçici hata: bir sonraki turda tekrar
      }
      if (!cancelled && status !== "done" && status !== "failed") {
        timer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [scanId, locale, status]);
  return events;
}

/** Tarayıcı bildirimi sadece kullanıcı isterse; izin ve bildirim tamamen tarayıcıda kalır. */
function useNotificationOptIn() {
  // Rapor istemcide yüklendikten sonra çizilir (polling), bu yüzden ilk değer doğrudan okunur
  const [state, setState] = useState<NotificationPermission | "unsupported">(() =>
    typeof window !== "undefined" && "Notification" in window
      ? Notification.permission
      : "unsupported",
  );
  return {
    supported: state !== "unsupported",
    state,
    request: async () => {
      if (!("Notification" in window)) return;
      setState(await Notification.requestPermission());
    },
  };
}
