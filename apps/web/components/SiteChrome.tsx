import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";
import { CONTACT_URL } from "@/lib/site";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { LogoMark } from "./Logo";

export async function Header() {
  const t = await getTranslations("Nav");
  return (
    <header className="sticky top-0 z-20 border-b border-line/70 bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between px-4">
        <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
          <LogoMark />
          <span className="text-lg">Launch Check</span>
        </Link>
        <nav className="flex items-center gap-2 sm:gap-4">
          <Link href="/privacy" className="text-sm text-muted transition hover:text-brand">
            {t("privacy")}
          </Link>
          <LocaleSwitcher />
        </nav>
      </div>
    </header>
  );
}

export async function Footer() {
  const t = await getTranslations("Footer");
  return (
    <footer className="mt-auto border-t border-line/70">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-3 px-4 py-8 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
        <p>{t("tagline")}</p>
        <div className="flex gap-5">
          <Link href="/privacy" className="hover:text-brand">
            {t("privacy")}
          </Link>
          <a href={CONTACT_URL} className="hover:text-brand">
            {t("contact")}
          </a>
        </div>
      </div>
    </footer>
  );
}
