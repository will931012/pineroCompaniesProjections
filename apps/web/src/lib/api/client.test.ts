import { afterEach, describe, expect, it, vi } from "vitest";
import { apiRequest, ApiError, setCsrfToken } from "./client";

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("apiRequest", () => {
  it("calls the same-origin API with query parameters", async () => {
    const fetchMock = mockFetch(200, { items: [], total: 0 });

    await apiRequest("/companies", { query: { query: "apple", limit: 5, skip: undefined } });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/companies?query=apple&limit=5");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "same-origin" });
  });

  it("surfaces the backend error envelope", async () => {
    mockFetch(404, { error: { code: "company_not_found", message: "No company.", request_id: "req-1" } });

    const error = await apiRequest("/companies/ZZZ").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, code: "company_not_found", requestId: "req-1" });
  });

  it("sends the CSRF token on unsafe methods only", async () => {
    const fetchMock = mockFetch(204, undefined);
    setCsrfToken("csrf-123");

    await apiRequest("/auth/logout", { method: "POST" });
    await apiRequest("/watchlists");

    expect(fetchMock.mock.calls[0][1].headers["X-CSRF-Token"]).toBe("csrf-123");
    expect(fetchMock.mock.calls[1][1].headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("reports network failures with a stable code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(apiRequest("/auth/session")).rejects.toMatchObject({ code: "network_error" });
  });
});
