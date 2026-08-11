import { localDate, localDateTime, timezoneAbbreviation, timezoneAbbreviationForLocalDate } from "./time";

describe("timezone-aware Today values", () => {
  it("uses the owner timezone rather than the browser timezone", () => {
    const instant = new Date("2026-08-10T02:30:45Z");
    expect(localDate(instant, "America/New_York")).toBe("2026-08-09");
    expect(localDateTime(instant, "America/New_York")).toBe("2026-08-09T22:30:45");
  });

  it("uses the IANA rules for the referenced instant, including daylight saving time", () => {
    expect(timezoneAbbreviation("America/New_York", "2026-01-15T12:00:00Z")).toBe("EST");
    expect(timezoneAbbreviation("America/New_York", "2026-08-15T12:00:00Z")).toBe("EDT");
    expect(timezoneAbbreviationForLocalDate("Europe/London", "2026-08-01")).toBe("GMT+1");
    expect(timezoneAbbreviationForLocalDate("Pacific/Auckland", "2026-09-27")).toBe("GMT+13");
  });
});
