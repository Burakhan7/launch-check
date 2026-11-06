import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";

export default async function NotFound() {
  const t = await getTranslations("NotFound");
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-4 px-4 py-24 text-center">
      <p className="text-6xl font-bold text-brand">404</p>
      <h1 className="text-2xl font-bold">{t("title")}</h1>
      <Link
        href="/"
        className="rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-brand-contrast hover:bg-brand-strong"
      >
        {t("back")}
      </Link>
    </div>
  );
}
