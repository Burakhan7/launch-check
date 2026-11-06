import { describe, expect, it } from "vitest";
import { ApiError, parseError } from "@/lib/api";

describe("parseError", () => {
  it("reads the API error envelope", async () => {
    const res = new Response(
      JSON.stringify({ error: { code: "invalid_url", message: "Geçersiz" } }),
      {
        status: 400,
      },
    );
    const err = await parseError(res);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.code, err.message, err.status]).toEqual(["invalid_url", "Geçersiz", 400]);
  });

  it("falls back to internal_error for non-JSON responses", async () => {
    const err = await parseError(new Response("<html>502</html>", { status: 502 }));
    expect([err.code, err.status]).toEqual(["internal_error", 502]);
  });
});
