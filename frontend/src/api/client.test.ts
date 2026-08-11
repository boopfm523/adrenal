import { apiRequest, getDailyGarminContext, login } from "./client";
import { sessionStore } from "./session";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("central API client", () => {
  beforeEach(() => {
    sessionStore.clear();
  });

  it("keeps the CSRF token in memory and sends it on writes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        csrf_token: "synthetic-csrf",
        email: "owner@example.test",
        display_name: "Synthetic Owner",
        default_timezone: "America/New_York",
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await login({ email: "owner@example.test", password: "synthetic-password" });
    await apiRequest<unknown>("/auth/logout", { method: "POST" });

    const loginRequest = fetchMock.mock.calls[0];
    const writeRequest = fetchMock.mock.calls[1];
    expect(loginRequest?.[0]).toBe("/api/v1/auth/login");
    expect(loginRequest?.[1]?.credentials).toBe("include");
    expect(new Headers(loginRequest?.[1]?.headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers(writeRequest?.[1]?.headers).get("X-CSRF-Token")).toBe("synthetic-csrf");
    expect(sessionStore.get()?.csrfToken).toBe("synthetic-csrf");
  });

  it("expires the central session on any 401 response", async () => {
    sessionStore.set({
      csrfToken: "synthetic-csrf",
      user: { email: "owner@example.test", displayName: null, defaultTimezone: "UTC" },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ detail: "not authenticated" }, 401));

    await expect(apiRequest("/timeline")).rejects.toEqual(expect.objectContaining({ status: 401 }));
    expect(sessionStore.get()).toBeNull();
  });

  it("collects paginated daily Garmin summaries with intraday samples", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/integrations/garmin/samples")) {
        return Promise.resolve(jsonResponse({ records: [{ id: "sample", kind: "sample", time: { occurred_at: "2026-08-10T12:00:00Z" } }], page: { total_pages: 1 } }));
      }
      const secondPage = url.includes("page=2");
      return Promise.resolve(jsonResponse({
        records: secondPage
          ? [{ id: "daily-2", kind: "daily", time: { occurred_at: "2026-08-10T05:00:00Z" } }]
          : [
              { id: "daily-1", kind: "daily", time: { occurred_at: "2026-08-10T04:00:00Z" } },
              { id: "sleep", kind: "sleep", time: { occurred_at: "2026-08-10T06:00:00Z" } },
            ],
        page: { total_pages: 2 },
      }));
    });

    const records = await getDailyGarminContext("2026-08-10", "America/New_York");
    expect(records.map((record) => record.id)).toEqual(["daily-1", "daily-2", "sample"]);
  });
});
