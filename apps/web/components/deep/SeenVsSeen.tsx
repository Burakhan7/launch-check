"use client";

import { useTranslations } from "next-intl";
import { assetUrl, type ScanReport } from "@/lib/api";
import { homeAsset, hostOf } from "@/lib/deep";
import { MeasureTag } from "../report/FindingCard";

interface Preview {
  url?: string;
  title?: string;
  description?: string;
  og_title?: string;
  og_description?: string;
  og_image?: string;
}

const SHOTS = [
  ["screenshot_desktop", "desktop"],
  ["screenshot_mobile", "mobile"],
  ["screenshot_nojs", "nojs"],
] as const;

/** "Gördüğün vs. gördükleri" (PHASE2 §8.3). Ekran görüntüleri bizim sunucumuzdan gelir.
 * WhatsApp ve Google kartları gerçek og/title verisiyle çizilen TEMSİLİ önizlemelerdir:
 * platform arayüzünün kopyası değil, marka logosu yok. og:image hedef siteden yüklenmez
 * (raporu açanın IP'si hedef siteye gitmesin); görselin yeri ve adresi gösterilir. */
export function SeenVsSeen({ report }: { report: ScanReport }) {
  const t = useTranslations("Deep.seen");
  const assets = report.sections?.assets ?? [];
  const preview = (report.meta.preview ?? {}) as Preview;
  const shots = SHOTS.map(([kind, key]) => ({
    key,
    asset: homeAsset(assets, kind, report.target_label),
  }));

  return (
    <section className="flex flex-col gap-4">
      <SectionHeader title={t("title")} hint={t("hint")} />
      <div className="grid gap-4 sm:grid-cols-3">
        {shots.map(({ key, asset }) => (
          <figure key={key} className="flex flex-col gap-2">
            <div className="overflow-hidden rounded-xl border border-line bg-surface">
              {asset ? (
                // eslint-disable-next-line @next/next/no-img-element -- API'den gelen WebP, optimize edilmiş
                <img
                  src={assetUrl(report.scan_id, asset.id)}
                  alt={t(`alt.${key}`)}
                  width={asset.width}
                  height={asset.height}
                  loading="lazy"
                  className="max-h-96 w-full object-cover object-top"
                />
              ) : (
                <p className="p-6 text-center text-xs text-muted">{t("noShot")}</p>
              )}
            </div>
            <figcaption className="text-xs text-muted">{t(`caption.${key}`)}</figcaption>
          </figure>
        ))}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <ShareCard preview={preview} />
        <SearchCard preview={preview} />
      </div>
    </section>
  );
}

function ShareCard({ preview }: { preview: Preview }) {
  const t = useTranslations("Deep.seen");
  const title = preview.og_title || preview.title || "";
  const description = preview.og_description || preview.description || "";
  return (
    <figure className="flex flex-col gap-2">
      <div className="overflow-hidden rounded-xl border border-line bg-surface">
        <div className="flex aspect-[1.91/1] items-center justify-center bg-surface-strong p-3 text-center text-xs text-muted">
          {preview.og_image ? t("imagePlaceholder", { url: preview.og_image }) : t("noImage")}
        </div>
        <div className="flex flex-col gap-0.5 p-3">
          <span className="text-[11px] text-muted uppercase">{hostOf(preview.url ?? "")}</span>
          <span className="line-clamp-2 text-sm font-semibold">{title || t("noTitle")}</span>
          <span className="line-clamp-2 text-xs text-muted">
            {description || t("noDescription")}
          </span>
        </div>
      </div>
      <figcaption className="text-xs text-muted">{t("shareCaption")}</figcaption>
    </figure>
  );
}

function SearchCard({ preview }: { preview: Preview }) {
  const t = useTranslations("Deep.seen");
  return (
    <figure className="flex flex-col gap-2">
      <div className="flex flex-col gap-1 rounded-xl border border-line bg-background p-4">
        <span className="truncate text-xs text-muted">{preview.url}</span>
        <span className="line-clamp-1 text-base font-medium text-brand">
          {preview.title || t("noTitle")}
        </span>
        <span className="line-clamp-2 text-sm text-muted">
          {preview.description || t("noDescription")}
        </span>
      </div>
      <figcaption className="text-xs text-muted">{t("searchCaption")}</figcaption>
    </figure>
  );
}

export function SectionHeader({
  title,
  hint,
  kind = "measure",
}: {
  title: string;
  hint?: string;
  kind?: "measure" | "comment";
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-xl font-semibold">{title}</h2>
        <MeasureTag kind={kind} />
      </div>
      {hint && <p className="text-sm text-muted">{hint}</p>}
    </div>
  );
}
