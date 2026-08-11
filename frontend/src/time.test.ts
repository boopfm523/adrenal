import { formatUnzonedDateTime, formatZonedDateTime, localDate, localDateTime, shiftIsoDate, timezoneAbbreviation, timezoneAbbreviationForLocalDate } from "./time";

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

  it("shifts local calendar dates across month, year, and leap-day boundaries", () => {
    expect(shiftIsoDate("2026-01-01", -1)).toBe("2025-12-31");
    expect(shiftIsoDate("2024-03-01", -1)).toBe("2024-02-29");
    expect(shiftIsoDate("2026-08-11", -2)).toBe("2026-08-09");
  });

  it("formats real instants in the requested timezone", () => {
    expect(formatZonedDateTime("2026-08-11T13:37:39.015204Z", "America/New_York")).toBe("Aug 11, 2026, 9:37 AM EDT");
  });

  it("formats timezone-less wall-clock values without shifting their fields", () => {
    expect(formatUnzonedDateTime("2026-08-11T09:36:00")).toBe("Aug 11, 2026, 9:36 AM");
    expect(formatUnzonedDateTime("not-a-time")).toBe("not-a-time");
  });
});
