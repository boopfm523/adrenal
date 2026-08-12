import { defaultHistoryDateRange, historyDateRangeFromSearch, setHistoryDateRange } from "./historyDates";

describe("history date ranges", () => {
  it("uses seven inclusive calendar days in the selected IANA timezone across DST", () => {
    const instant = new Date("2026-03-08T03:30:00Z");
    expect(defaultHistoryDateRange("America/New_York", instant)).toEqual({
      dateFrom: "2026-03-01",
      dateTo: "2026-03-07",
      allHistory: false,
    });
    expect(defaultHistoryDateRange("Asia/Tokyo", instant)).toEqual({
      dateFrom: "2026-03-02",
      dateTo: "2026-03-08",
      allHistory: false,
    });
  });

  it("keeps explicit URL dates authoritative and represents all history explicitly", () => {
    const explicit = new URLSearchParams("local_date_from=2025-01-02&local_date_to=2025-01-03");
    expect(historyDateRangeFromSearch(explicit, "UTC", new Date("2026-08-12T00:00:00Z"))).toEqual({
      dateFrom: "2025-01-02",
      dateTo: "2025-01-03",
      allHistory: false,
    });
    const all = historyDateRangeFromSearch(new URLSearchParams("history=all"), "UTC");
    expect(all).toEqual({ dateFrom: "", dateTo: "", allHistory: true });
    const serialized = new URLSearchParams();
    setHistoryDateRange(serialized, all);
    expect(serialized.toString()).toBe("history=all");
  });
});
