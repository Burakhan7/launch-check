import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { ReportView } from "@/components/report/ReportView";

// Kullanıcı raporları arama motorlarında çıkmamalı (AGENTS.md §14).
export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Meta");
  return {
    title: t("reportTitle"),
    robots: { index: false, follow: false, nocache: true },
    referrer: "no-referrer",
  };
}

export default async function ReportPage() {
  const t = await getTranslations("Report");
  // Rapor tamamen istemcide yüklenir (polling); statik kabuk anında gelir.
  return (
    <Suspense fallback={<p className="py-24 text-center text-muted">{t("loading")}</p>}>
      <ReportView />
    </Suspense>
  );
}
