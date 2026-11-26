// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanChecklist } from "@/components/deep/PlanChecklist";
import type { Finding } from "@/lib/api";
import { readDone, writeDone } from "@/lib/planStorage";
import { browserStorage } from "@/lib/storage";
import { applyTheme, readTheme, THEME_KEY, writeTheme } from "@/lib/theme";

const throwing = {
  getItem: () => {
    throw new Error("QuotaExceeded");
  },
  setItem: () => {
    throw new Error("QuotaExceeded");
  },
  removeItem: () => {
    throw new Error("SecurityError");
  },
};

function finding(id: string, title: string): Finding {
  return { id, check_id: `web.deep.${id}`, title, severity: "high" } as unknown as Finding;
}

/** Safari'de çerezler kapalıyken olduğu gibi: localStorage'a erişmek bile hata atar. */
function blockStorage() {
  const original = Object.getOwnPropertyDescriptor(window, "localStorage");
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() {
      throw new DOMException("denied", "SecurityError");
    },
  });
  return () => Object.defineProperty(window, "localStorage", original!);
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("planStorage", () => {
  it("bozuk JSON ve hata atan depolamada boş liste döner", () => {
    expect(readDone(throwing, "s1")).toEqual([]);
    expect(readDone({ getItem: () => "{bozuk" }, "s1")).toEqual([]);
    expect(readDone({ getItem: () => '{"a":1}' }, "s1")).toEqual([]);
    expect(readDone({ getItem: () => '["a", 3, "b"]' }, "s1")).toEqual(["a", "b"]);
    expect(readDone(undefined, "s1")).toEqual([]);
  });

  it("yazma hatası sessizce false döner", () => {
    expect(writeDone(throwing, "s1", ["a"])).toBe(false);
    expect(writeDone(window.localStorage, "s1", ["a"])).toBe(true);
    expect(readDone(window.localStorage, "s1")).toEqual(["a"]);
  });

  it("erişimi engellenmiş localStorage undefined olur", () => {
    const restore = blockStorage();
    try {
      expect(browserStorage()).toBeUndefined();
    } finally {
      restore();
    }
  });
});

describe("PlanChecklist", () => {
  it("işaretler localStorage'a yazılır ve diğer grupların işaretleri korunur", () => {
    window.localStorage.setItem("lc-plan-s1", JSON.stringify(["other"]));
    render(
      <PlanChecklist
        scanId="s1"
        findings={[finding("a", "Birinci"), finding("b", "İkinci")]}
        minutesLabel={(m) => `${m} dk`}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "Birinci" }));
    expect(JSON.parse(window.localStorage.getItem("lc-plan-s1")!)).toEqual(["other", "a"]);
    expect(screen.getByRole("checkbox", { name: "Birinci" }).getAttribute("aria-checked")).toBe(
      "true",
    );
  });

  it("localStorage erişimi hata atarsa liste yine çalışır (işaret sadece bu sayfada)", () => {
    const restore = blockStorage();
    try {
      render(
        <PlanChecklist scanId="s1" findings={[finding("a", "Birinci")]} minutesLabel={String} />,
      );
      const box = screen.getByRole("checkbox", { name: "Birinci" });
      expect(box.getAttribute("aria-checked")).toBe("false");
      fireEvent.click(box);
      expect(box.getAttribute("aria-checked")).toBe("true");
    } finally {
      restore();
    }
  });
});

describe("tema", () => {
  it("geçersiz veya hatalı depolamada sistem temasına düşer", () => {
    expect(readTheme(throwing)).toBeNull();
    expect(readTheme({ getItem: () => "mor" })).toBeNull();
    expect(() => writeTheme(throwing, "dark")).not.toThrow();
    expect(() => writeTheme(undefined, null)).not.toThrow();
  });

  it("seçim yazılır, silinir ve data-theme çözülmüş değeri taşır", () => {
    writeTheme(window.localStorage, "dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(readTheme(window.localStorage)).toBe("dark");
    writeTheme(window.localStorage, null);
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();

    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as never;
    applyTheme(null);
    expect(document.documentElement.dataset.theme).toBe("dark");
    window.matchMedia = vi.fn().mockReturnValue({ matches: false }) as never;
    applyTheme(null);
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
