import { localDate, localDateTime } from "./time";

describe("timezone-aware Today values", () => {
  it("uses the owner timezone rather than the browser timezone", () => {
    const instant = new Date("2026-08-10T02:30:45Z");
    expect(localDate(instant, "America/New_York")).toBe("2026-08-09");
    expect(localDateTime(instant, "America/New_York")).toBe("2026-08-09T22:30:45");
  });
});
