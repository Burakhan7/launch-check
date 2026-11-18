import { describe, expect, it } from "vitest";
import type { Category, Finding, Severity } from "@/lib/api";
import { buildCombinedPrompt, orderFindings, type TranslateFn } from "@/lib/combinedPrompt";
import en from "@/messages/en.json";
import tr from "@/messages/tr.json";

/** Basit {yer_tutucu} doldurucu: mesaj dosyalarındaki gerçek metinlerle test eder. */
function translator(messages: Record<string, string>): TranslateFn {
  return (key, values = {}) =>
    messages[key].replace(/\{(\w+)\}/g, (_, name: string) => String(values[name] ?? ""));
}

function finding(
  id: string,
  severity: Severity,
  category: Category = "security",
  evidence: Record<string, unknown> = {},
): Finding {
  return {
    check_id: id,
    severity,
    category,
    title: `Title ${id}`,
    description: "",
    why_it_matters: "",
    fix_steps: [],
    fix_prompt: `Fix ${id}.\nNote: I will rotate it myself.`,
    evidence,
  };
}

const FINDINGS: Finding[] = [
  finding("low.meta", "low", "prod_readiness", { pages: ["/", "/about"] }),
  finding("info.exif", "info"),
  finding("high.conv", "high", "conversion"),
  finding("high.sec", "high", "security", { path: "/.env", keys: ["DATABASE_URL"] }),
  finding("crit.key", "critical", "security", {
    kind: "openai",
    masked: "sk-pro…ELCl",
    locations: [
      { file: "/assets/a.js", line: 1 },
      { file: "/assets/b.js", line: 7 },
      { file: "/assets/c.js", line: 2 },
      { file: "/assets/d.js", line: 9 },
    ],
  }),
  finding("high.prod", "high", "prod_readiness"),
];

const opts = (messages: Record<string, string>, maxChars?: number) => ({
  target: "https://shop.example.com",
  platforms: ["lovable", "supabase"],
  t: translator(messages),
  severityLabel: (s: Severity) => s.toUpperCase(),
  maxChars,
});

describe("buildCombinedPrompt", () => {
  it("orders by severity, then category, then API order and drops info", () => {
    expect(orderFindings(FINDINGS).map((f) => f.check_id)).toEqual([
      "crit.key",
      "high.sec",
      "high.prod",
      "high.conv",
      "low.meta",
    ]);
  });

  it("excludes info findings from the count", () => {
    const result = buildCombinedPrompt(FINDINGS, opts(tr.CombinedPrompt));
    expect(result.included).toBe(5);
    expect(result.omitted).toBe(0);
    expect(result.text).not.toContain("info.exif");
  });

  it("writes at most 3 locations and never the masked value", () => {
    const { text } = buildCombinedPrompt(FINDINGS, opts(en.CombinedPrompt));
    expect(text).toContain("Location: /assets/a.js:1, /assets/b.js:7, /assets/c.js:2");
    expect(text).not.toContain("/assets/d.js");
    expect(text).not.toContain("sk-pro");
    expect(text).not.toContain("DATABASE_URL");
    expect(text).toContain("Location: /.env");
  });

  it("keeps each finding's own note lines", () => {
    const { text } = buildCombinedPrompt(FINDINGS, opts(en.CombinedPrompt));
    expect(text.match(/Note: I will rotate it myself\./g)).toHaveLength(5);
  });

  it("never splits a finding when over maxChars and counts omitted", () => {
    const full = buildCombinedPrompt(FINDINGS, opts(tr.CombinedPrompt)).text;
    // Son bulgu sığmaz; yerine kısa kesme satırı eklenir
    const limit = full.length - 1;
    const result = buildCombinedPrompt(FINDINGS, opts(tr.CombinedPrompt, limit));
    expect(result.included).toBe(4);
    expect(result.omitted).toBe(1);
    expect(result.text.length).toBeLessThanOrEqual(limit);
    expect(result.text).not.toContain("low.meta");
    expect(result.text.endsWith("Kalan 1 sorunu bunlar bitince siteyi tekrar tarayarak al.")).toBe(
      true,
    );
  });

  it("returns empty text when there is nothing to fix", () => {
    expect(buildCombinedPrompt([finding("i", "info")], opts(tr.CombinedPrompt))).toEqual({
      text: "",
      included: 0,
      omitted: 0,
    });
  });

  it("omits the platform part when no platform is known", () => {
    const { text } = buildCombinedPrompt(FINDINGS.slice(0, 1), {
      ...opts(en.CombinedPrompt),
      platforms: [],
    });
    expect(text).toContain("(target: https://shop.example.com)");
  });

  it("matches the Turkish snapshot", () => {
    expect(buildCombinedPrompt(FINDINGS.slice(3, 5), opts(tr.CombinedPrompt)).text)
      .toMatchInlineSnapshot(`
        "Projemde yayın öncesi taramada aşağıdaki sorunlar bulundu (hedef: https://shop.example.com, platform: lovable, supabase). Sırayla ilerle; her birini bitirince kısaca ne değiştirdiğini yaz. Benim panelden yapmam gereken adımları ayrıca listele.

        ## 1. Title crit.key  [CRITICAL]
        Konum: /assets/a.js:1, /assets/b.js:7, /assets/c.js:2
        Fix crit.key.
        Note: I will rotate it myself.

        ## 2. Title high.sec  [HIGH]
        Konum: /.env
        Fix high.sec.
        Note: I will rotate it myself.

        Bitince: yaptığın değişikliklerin listesini ve benim yapmam gereken manuel adımları (anahtar yenileme, panel ayarları) ayrı bir başlık altında ver."
      `);
  });

  it("matches the English snapshot", () => {
    expect(buildCombinedPrompt(FINDINGS.slice(3, 5), opts(en.CombinedPrompt)).text)
      .toMatchInlineSnapshot(`
        "A pre-launch scan of my project found the issues below (target: https://shop.example.com, platform: lovable, supabase). Work through them in order; after finishing each one, briefly write what you changed. List the steps I need to do in dashboards separately.

        ## 1. Title crit.key  [CRITICAL]
        Location: /assets/a.js:1, /assets/b.js:7, /assets/c.js:2
        Fix crit.key.
        Note: I will rotate it myself.

        ## 2. Title high.sec  [HIGH]
        Location: /.env
        Fix high.sec.
        Note: I will rotate it myself.

        When you're done: give me a list of the changes you made and, under a separate heading, the manual steps I need to do (rotating keys, dashboard settings)."
      `);
  });
});
