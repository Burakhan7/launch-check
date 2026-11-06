"use client";

import { useEffect, useState } from "react";

const RESET_MS = 2000;

export function CopyButton({
  text,
  label,
  copiedLabel,
  variant = "solid",
}: {
  text: string | (() => string);
  label: string;
  copiedLabel: string;
  variant?: "solid" | "outline";
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const id = setTimeout(() => setCopied(false), RESET_MS);
    return () => clearTimeout(id);
  }, [copied]);

  async function copy() {
    const value = typeof text === "function" ? text() : text;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Eski tarayıcılar / izin yok: gizli textarea ile yedek yöntem
      const area = document.createElement("textarea");
      area.value = value;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setCopied(true);
  }

  const style =
    variant === "solid"
      ? "bg-brand text-brand-contrast hover:bg-brand-strong"
      : "border border-line text-foreground hover:border-brand hover:text-brand";
  return (
    <button
      type="button"
      onClick={copy}
      aria-live="polite"
      className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold transition ${style}`}
    >
      {copied ? <CheckGlyph /> : <CopyGlyph />}
      {copied ? copiedLabel : label}
    </button>
  );
}

function CopyGlyph() {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <rect x="7" y="7" width="10" height="10" rx="2" />
      <path d="M13 7V5a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2" />
    </svg>
  );
}

function CheckGlyph() {
  return (
    <svg
      viewBox="0 0 20 20"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      aria-hidden="true"
    >
      <path d="M4.5 10.5l3.5 3.5 7.5-8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
