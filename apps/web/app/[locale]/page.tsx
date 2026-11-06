import { getTranslations } from "next-intl/server";
import { ScanForm } from "@/components/ScanForm";
import { Link } from "@/i18n/navigation";

const CHECK_KEYS = ["secrets", "files", "build", "network", "headers", "launch"] as const;
const HOW_KEYS = ["one", "two", "three"] as const;

export default async function HomePage() {
  const t = await getTranslations("Home");
  return (
    <div className="hero-glow">
      <section className="mx-auto flex w-full max-w-3xl flex-col items-center gap-6 px-4 pt-14 pb-10 text-center sm:pt-20">
        <span className="rounded-full bg-brand-soft px-3 py-1 text-xs font-semibold text-brand">
          {t("badge")}
        </span>
        <h1 className="text-4xl font-bold tracking-tight text-balance sm:text-5xl">{t("title")}</h1>
        <p className="max-w-2xl text-lg text-pretty text-muted">{t("subtitle")}</p>
        <div className="mt-2 w-full text-left">
          <ScanForm />
        </div>
        <p className="text-sm text-muted">
          {t("privacyNote")}{" "}
          <Link
            href="/privacy"
            className="font-medium text-brand underline-offset-4 hover:underline"
          >
            {t("privacyLink")}
          </Link>
        </p>
      </section>

      <section className="mx-auto grid w-full max-w-5xl gap-10 px-4 pb-20 md:grid-cols-2">
        <div>
          <h2 className="mb-4 text-xl font-semibold">{t("checksTitle")}</h2>
          <ul className="grid gap-3">
            {CHECK_KEYS.map((key) => (
              <li key={key} className="flex gap-3 rounded-2xl border border-line bg-surface p-4">
                <CheckIcon />
                <span className="text-sm">{t(`checks.${key}`)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h2 className="mb-4 text-xl font-semibold">{t("howTitle")}</h2>
          <ol className="grid gap-3">
            {HOW_KEYS.map((key, i) => (
              <li key={key} className="flex items-start gap-3 rounded-2xl border border-line p-4">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand text-sm font-bold text-brand-contrast">
                  {i + 1}
                </span>
                <span className="text-sm">{t(`how.${key}`)}</span>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </div>
  );
}

function CheckIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      className="mt-0.5 h-5 w-5 shrink-0 text-brand"
      fill="currentColor"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.7-9.3a1 1 0 00-1.4-1.4L9 10.6 7.7 9.3a1 1 0 00-1.4 1.4l2 2a1 1 0 001.4 0l4-4z"
        clipRule="evenodd"
      />
    </svg>
  );
}
