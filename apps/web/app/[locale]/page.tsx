import { getTranslations } from "next-intl/server";

export default async function HomePage() {
  const t = await getTranslations("Home");
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-4 px-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{t("title")}</h1>
      <p className="text-lg opacity-80">{t("subtitle")}</p>
      <p className="text-sm opacity-60">{t("comingSoon")}</p>
    </main>
  );
}
