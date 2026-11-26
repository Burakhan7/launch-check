import { describe, expect, it } from "vitest";
import type { Finding, ScanReport } from "@/lib/api";
import {
  canStartDeep,
  homeAsset,
  hostOf,
  phase1Findings,
  phase2Findings,
  planGroups,
  rebuildPrompt,
  seconds,
} from "@/lib/deep";

const NOW = Date.parse("2026-11-26T12:00:00Z");

function finding(id: string, extra: Partial<Finding> = {}): Finding {
  return {
    id,
    check_id: `web.x.${id}`,
    severity: "high",
    category: "security",
    title: id,
    description: "",
    why_it_matters: "",
    fix_steps: [],
    fix_prompt: "fix",
    evidence: {},
    ...extra,
  };
}

function report(extra: Partial<ScanReport> = {}): ScanReport {
  return {
    scan_id: "s1",
    status: "done",
    target_type: "web",
    target_label: "https://acme.test",
    phase: 1,
    score: 70,
    score_label: "needs_work",
    category_scores: null,
    meta: {},
    error: null,
    findings: [],
    passed: [],
    created_at: new Date(NOW - 60 * 60 * 1000).toISOString(),
    finished_at: null,
    expires_at: new Date(NOW).toISOString(),
    ...extra,
  };
}

describe("canStartDeep", () => {
  it("allows a finished, recent phase 1 web scan", () => {
    expect(canStartDeep(report(), NOW)).toBe(true);
  });
  it("rejects apk, phase 2, unfinished and old scans", () => {
    expect(canStartDeep(report({ target_type: "apk" }), NOW)).toBe(false);
    expect(canStartDeep(report({ phase: 2 }), NOW)).toBe(false);
    expect(canStartDeep(report({ status: "running" }), NOW)).toBe(false);
    const old = new Date(NOW - 25 * 60 * 60 * 1000).toISOString();
    expect(canStartDeep(report({ created_at: old }), NOW)).toBe(false);
  });
});

describe("planGroups", () => {
  it("maps plan ids to findings and skips unknown ids", () => {
    const r = report({
      phase: 2,
      findings: [finding("a"), finding("b"), finding("c")],
      sections: {
        phase_diff: {} as never,
        plan: { today: ["a", "missing"], week: ["b"], month: ["c"] },
        gates: {},
        rescan: null,
        assets: [],
      },
    });
    const groups = planGroups(r);
    expect(groups.today.map((f) => f.id)).toEqual(["a"]);
    expect(groups.week.map((f) => f.id)).toEqual(["b"]);
    expect(groups.month.map((f) => f.id)).toEqual(["c"]);
  });
  it("is empty without sections", () => {
    expect(planGroups(report())).toEqual({ today: [], week: [], month: [] });
  });
});

describe("phase split", () => {
  it("separates phase 1 copies from new findings", () => {
    const r = report({
      findings: [
        finding("a", { source_phase: 1 }),
        finding("b", { source_phase: 2 }),
        finding("c"),
      ],
    });
    expect(phase1Findings(r).map((f) => f.id)).toEqual(["a", "c"]);
    expect(phase2Findings(r).map((f) => f.id)).toEqual(["b"]);
  });
});

describe("homeAsset", () => {
  const assets = [
    {
      id: "1",
      kind: "screenshot_mobile",
      page_url: "https://acme.test/pricing",
      width: 1,
      height: 1,
    },
    { id: "2", kind: "screenshot_mobile", page_url: "https://acme.test/", width: 1, height: 1 },
    {
      id: "3",
      kind: "screenshot_desktop",
      page_url: "https://acme.test/pricing",
      width: 1,
      height: 1,
    },
  ];
  it("prefers the home page and falls back to any page", () => {
    expect(homeAsset(assets, "screenshot_mobile", "https://acme.test")?.id).toBe("2");
    expect(homeAsset(assets, "screenshot_desktop", "https://acme.test")?.id).toBe("3");
    expect(homeAsset(assets, "screenshot_nojs", "https://acme.test")).toBeNull();
  });
});

describe("rebuildPrompt", () => {
  it("lists sections in order with the user's own title and rules", () => {
    const t = (key: string, values?: Record<string, string | number>) =>
      `${key}${values ? JSON.stringify(values) : ""}`;
    const text = rebuildPrompt(
      ["hero", "logos", "faq"],
      { target: "https://acme.test", title: "Acme" },
      t,
      (k) => k.toUpperCase(),
    );
    const lines = text.split("\n");
    expect(lines[0]).toContain('"title":"Acme"');
    expect(lines.slice(1, 4)).toEqual([
      "1. HERO: rebuildSection.hero",
      "2. LOGOS: rebuildSection.logos",
      "3. FAQ: rebuildSection.faq",
    ]);
    expect(lines[4]).toBe("rebuildRules");
  });
  it("falls back to the target when there's no title", () => {
    const t = (key: string, values?: Record<string, string | number>) =>
      String(values?.title ?? key);
    expect(rebuildPrompt([], { target: "https://acme.test", title: "" }, t, (k) => k)).toContain(
      "https://acme.test",
    );
  });
});

describe("formatting", () => {
  it("formats seconds and hosts", () => {
    expect(seconds(2150)).toBe("2.1");
    expect(seconds(null)).toBe("—");
    expect(hostOf("https://www.acme.test/x")).toBe("www.acme.test");
    expect(hostOf("not a url")).toBe("not a url");
  });
});
