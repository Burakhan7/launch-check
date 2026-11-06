"use client";

import { useLocale, useTranslations } from "next-intl";
import { Suspense } from "react";
import { Link, usePathname } from "@/i18n/navigation";

const STYLE =
  "rounded-full border border-line px-3 py-1.5 text-sm font-medium text-muted transition hover:border-brand hover:text-brand";

function useOther() {
  const locale = useLocale();
  return locale === "tr" ? "en" : "tr";
}

/** Aynı sayfanın diğer dildeki hâline gider. */
function CurrentPageSwitcher() {
  const t = useTranslations("Nav");
  const pathname = usePathname();
  return (
    <Link
      href={pathname}
      locale={useOther()}
      aria-label={t("switchLanguageLabel")}
      className={STYLE}
    >
      {t("switchLanguage")}
    </Link>
  );
}

/** Dinamik sayfalarda (rapor) adres önceden bilinmez; o sırada ana sayfaya gider. */
function FallbackSwitcher() {
  const t = useTranslations("Nav");
  return (
    <Link href="/" locale={useOther()} aria-label={t("switchLanguageLabel")} className={STYLE}>
      {t("switchLanguage")}
    </Link>
  );
}

export function LocaleSwitcher() {
  return (
    <Suspense fallback={<FallbackSwitcher />}>
      <CurrentPageSwitcher />
    </Suspense>
  );
}
