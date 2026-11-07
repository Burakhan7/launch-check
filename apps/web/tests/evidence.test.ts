import { describe, expect, it } from "vitest";
import { evidenceRows } from "@/lib/evidence";

describe("evidenceRows", () => {
  it("formats secret evidence with masked value and locations", () => {
    const rows = evidenceRows({
      kind: "openai",
      masked: "sk-pro…ELCl",
      locations: [{ file: "assets/a.js", line: 3 }],
      location_count: 14,
    });
    expect(rows).toEqual([
      { key: "masked", value: { kind: "code", value: "sk-pro…ELCl" } },
      { key: "locations", value: { kind: "list", items: ["assets/a.js:3"], more: 13 } },
    ]);
  });

  it("hides helper keys and empty lists, flattens objects", () => {
    const rows = evidenceRows({
      count: 1,
      searched: ["html"],
      urls: [],
      headers: { server: "nginx/1.18.0" },
      components: [{ type: "service", name: ".Sync" }],
    });
    expect(rows.map((r) => r.key)).toEqual(["headers", "components"]);
    expect(rows[0].value).toEqual({ kind: "list", items: ["server: nginx/1.18.0"], more: 0 });
    expect(rows[1].value).toEqual({ kind: "list", items: ["service .Sync"], more: 0 });
  });

  it("translates values through the callback", () => {
    const rows = evidenceRows({ reasons: ["no_js_bundle"] }, (_k, v) => `T:${v}`);
    expect(rows[0].value).toEqual({ kind: "list", items: ["T:no_js_bundle"], more: 0 });
  });

  it("caps long lists at 10 items", () => {
    const urls = Array.from({ length: 15 }, (_, i) => `http://localhost:${3000 + i}`);
    const rows = evidenceRows({ urls, count: 15 });
    expect(rows[0].value).toMatchObject({ kind: "list", more: 5 });
    if (rows[0].value.kind === "list") expect(rows[0].value.items).toHaveLength(10);
  });
});

describe("page evidence", () => {
  it("renders per-page objects as readable lines", () => {
    const rows = evidenceRows({
      home_url: "https://x.com/tr",
      pages: [
        { url: "https://x.com/tr", missing: ["og:image"], issues: ["twitter_card_without_image"] },
        { url: "https://x.com/shop", main_content_words: 1, empty_shell: false },
      ],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].value).toEqual({
      kind: "list",
      items: [
        "https://x.com/tr — og:image, twitter_card_without_image",
        "https://x.com/shop — main_content_words: 1",
      ],
      more: 0,
    });
  });
});

describe("generic object evidence", () => {
  it("uses a primary field and lists the rest", () => {
    const rows = evidenceRows({
      duplicate_titles: [
        { title: "Vakıf Genel Merkezi", pages: ["https://a/", "https://a/iletisim"] },
      ],
      forms: [
        {
          page: "https://a/iletisim",
          fields: ["email", "name"],
          has_consent: false,
          action: "https://a/send",
          issues: ["no_consent"],
        },
      ],
      majority_same_title: true,
    });
    expect(rows.map((r) => r.key)).toEqual(["duplicate_titles", "forms"]);
    expect(rows[0].value).toMatchObject({
      items: ["Vakıf Genel Merkezi — https://a/, https://a/iletisim"],
    });
    expect(rows[1].value).toMatchObject({
      items: ["https://a/iletisim — email, name, no_consent"],
    });
  });
});
