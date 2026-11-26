import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { CONTACT_URL } from "@/lib/site";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Meta");
  return { title: t("botTitle") };
}

const DOES = ["pages", "banner", "files"] as const;
const DOES_NOT = ["forms", "apis", "competitors", "load"] as const;
const ROBOTS = "User-agent: LaunchCheckBot\nDisallow: /";

/** Botun ne yaptığını ve nasıl engellenebileceğini anlatan sayfa (PHASE2 §12.4). Kullanıcı
 * ajanındaki adres buraya işaret eder. */
export default async function BotPage() {
  const t = await getTranslations("Bot");
  return (
    <article className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-12">
      <header className="flex flex-col gap-3">
        <h1 className="font-mono text-3xl font-bold">{t("title")}</h1>
        <p className="text-lg text-muted">{t("intro")}</p>
      </header>
      <section className="flex flex-col gap-2">
        <h2 className="text-xl font-semibold">{t("uaTitle")}</h2>
        <p className="text-muted">{t("ua")}</p>
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        <List title={t("doesTitle")} items={DOES.map((k) => t(`does.${k}`))} />
        <List title={t("doesNotTitle")} items={DOES_NOT.map((k) => t(`doesNot.${k}`))} />
      </div>
      <section className="flex flex-col gap-3">
        <h2 className="text-xl font-semibold">{t("blockTitle")}</h2>
        <p className="text-muted">{t("block")}</p>
        <pre className="rounded-xl border border-line bg-surface p-4 font-mono text-sm">
          {ROBOTS}
        </pre>
        <p className="text-sm text-muted">{t("blockNote")}</p>
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="text-xl font-semibold">{t("contactTitle")}</h2>
        <p className="text-muted">
          {t("contact")}{" "}
          <a
            href={CONTACT_URL}
            className="font-medium text-brand underline-offset-4 hover:underline"
          >
            {CONTACT_URL.replace(/^mailto:/, "")}
          </a>
        </p>
      </section>
    </article>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5">
      <h2 className="font-semibold">{title}</h2>
      <ul className="flex list-disc flex-col gap-2 pl-5 text-sm">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}
