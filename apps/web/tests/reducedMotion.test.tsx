// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// motion, tercihi ilk kullanımda bir kez okur: bileşenler yüklenmeden önce ayarlanır
let reduce = true;
beforeAll(() => {
  window.matchMedia = ((query: string) => ({
    matches: query.includes("reduced-motion") && reduce,
    media: query,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  })) as never;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** WebGL çağrılarını yutan sahte bağlam: sadece animasyon döngüsünü test ederiz. */
function fakeGl() {
  return new Proxy(
    { getProgramParameter: () => true, getAttribLocation: () => 0 },
    {
      get: (target, key) =>
        key in target ? target[key as keyof typeof target] : () => ({}) as unknown,
    },
  );
}

describe("prefers-reduced-motion", () => {
  it("AI Status parıltı animasyonu yerine düz metin gösterir", async () => {
    const { AiStatus } = await import("@/components/rare/ai-status");
    render(<AiStatus state="analyzing" label="Görünüm" timer={false} />);
    const text = screen.getByText("Görünüm");
    expect(text.className).toContain("text-muted");
    expect(text.className).not.toContain("bg-clip-text");
  });

  it("Fluid Orb tek kare çizer, animasyon döngüsü başlatmaz", async () => {
    reduce = true;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeGl() as never);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    const { default: FluidOrb } = await import("@/components/rare/fluid-orb");
    render(<FluidOrb size={40} />);
    expect(raf).not.toHaveBeenCalled();
    document.dispatchEvent(new Event("visibilitychange"));
    expect(raf).not.toHaveBeenCalled();
  });
});

describe("Fluid Orb sekme görünürlüğü", () => {
  it("sekme gizlenince durur, görünür olunca devam eder", async () => {
    reduce = false;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(fakeGl() as never);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(7);
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    const { default: FluidOrb } = await import("@/components/rare/fluid-orb");
    render(<FluidOrb size={40} />);
    expect(raf).toHaveBeenCalledTimes(1);

    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(cancel).toHaveBeenCalledWith(7);
    expect(raf).toHaveBeenCalledTimes(1);

    visibility.mockReturnValue("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(raf).toHaveBeenCalledTimes(2);
  });
});
