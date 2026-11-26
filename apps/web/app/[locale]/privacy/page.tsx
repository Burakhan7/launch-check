import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { CONTACT_URL } from "@/lib/site";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Meta");
  return { title: t("privacyTitle") };
}

const STORED = ["report", "evidence", "rate"] as const;
const NOT_STORED = ["apk", "filename", "account", "secrets"] as const;
const DEEP = [
  "screenshots",
  "visitors",
  "competitors",
  "google",
  "services",
  "ai",
  "values",
  "local",
] as const;
const CREDITS = [
  "easyprivacy",
  "retire",
  "axe",
  "takeover",
  "majestic",
  "playwright",
  "rareui",
] as const;

export default async function PrivacyPage() {
  const t = await getTranslations("Privacy");
  return (
    <article className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-12">
      <header className="flex flex-col gap-3">
        <h1 className="text-3xl font-bold">{t("title")}</h1>
        <p className="text-lg text-muted">{t("intro")}</p>
      </header>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title={t("storedTitle")} tone="brand">
          {STORED.map((key) => (
            <li key={key}>{t(`stored.${key}`)}</li>
          ))}
        </Card>
        <Card title={t("notStoredTitle")} tone="good">
          {NOT_STORED.map((key) => (
            <li key={key}>{t(`notStored.${key}`)}</li>
          ))}
        </Card>
      </div>

      <Block title={t("retentionTitle")} text={t("retention")} />
      <Block title={t("scanTitle")} text={t("scan")} />
      <section className="flex flex-col gap-2">
        <h2 className="text-xl font-semibold">{t("deepTitle")}</h2>
        <ul className="flex list-disc flex-col gap-2 pl-5 text-muted">
          {DEEP.map((key) => (
            <li key={key}>{t(`deep.${key}`)}</li>
          ))}
        </ul>
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="text-xl font-semibold">{t("creditsTitle")}</h2>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted">
          {CREDITS.map((key) => (
            <li key={key}>{t(`credits.${key}`)}</li>
          ))}
        </ul>
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

function Card({
  title,
  tone,
  children,
}: {
  title: string;
  tone: "brand" | "good";
  children: ReactNode;
}) {
  const accent = tone === "brand" ? "text-brand" : "text-score-good";
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5">
      <h2 className={`font-semibold ${accent}`}>{title}</h2>
      <ul className="flex list-disc flex-col gap-2 pl-5 text-sm">{children}</ul>
    </section>
  );
}

function Block({ title, text }: { title: string; text: string }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-xl font-semibold">{title}</h2>
      <p className="text-muted">{text}</p>
    </section>
  );
}
