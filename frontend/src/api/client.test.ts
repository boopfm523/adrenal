import { apiRequest, login } from "./client";
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
});
