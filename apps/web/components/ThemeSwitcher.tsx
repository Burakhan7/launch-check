"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { browserStorage } from "@/lib/storage";
import { applyTheme, readTheme, writeTheme, type ThemeChoice } from "@/lib/theme";
import { DEFAULT_THEMES, ThemeSelector } from "./rare/theme-selector";

/** Alt bilgideki tema seçici (Rare UI Theme selector). Seçim yoksa sistem teması. */
export default function ThemeSwitcher() {
  const t = useTranslations("Theme");
  const [choice, setChoice] = useState<ThemeChoice | null>(() => readTheme(browserStorage()));
  const themes = DEFAULT_THEMES.map((theme) => ({
    ...theme,
    label: t(theme.value as ThemeChoice),
  }));

  function pick(value: ThemeChoice | null) {
    setChoice(value);
    writeTheme(browserStorage(), value);
    applyTheme(value);
  }

  return (
    <div className="flex items-center gap-3">
      <ThemeSelector
        themes={themes}
        value={choice}
        onChange={(value) => pick(value as ThemeChoice)}
        labels={false}
        groupLabel={t("label")}
        className="gap-2!"
        itemClassName="w-14!"
      />
      <button
        type="button"
        onClick={() => pick(null)}
        aria-pressed={choice === null}
        className={`text-xs ${choice === null ? "font-semibold text-foreground" : "text-muted hover:text-brand"}`}
      >
        {t("system")}
      </button>
    </div>
  );
}
