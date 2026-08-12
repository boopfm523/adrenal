import { localDate, shiftIsoDate } from "./time";

export interface HistoryDateRange {
  dateFrom: string;
  dateTo: string;
  allHistory: boolean;
}

export function defaultHistoryDateRange(timezone: string, now = new Date()): HistoryDateRange {
  const dateTo = localDate(now, timezone);
  return { dateFrom: shiftIsoDate(dateTo, -6), dateTo, allHistory: false };
}

export function historyDateRangeFromSearch(params: URLSearchParams, timezone: string, now = new Date()): HistoryDateRange {
  if (params.get("history") === "all") return { dateFrom: "", dateTo: "", allHistory: true };
  if (params.has("local_date_from") || params.has("local_date_to")) {
    return {
      dateFrom: params.get("local_date_from") ?? "",
      dateTo: params.get("local_date_to") ?? "",
      allHistory: false,
    };
  }
  return defaultHistoryDateRange(timezone, now);
}

export function setHistoryDateRange(params: URLSearchParams, range: HistoryDateRange): void {
  if (range.allHistory) {
    params.set("history", "all");
    return;
  }
  if (range.dateFrom !== "") params.set("local_date_from", range.dateFrom);
  if (range.dateTo !== "") params.set("local_date_to", range.dateTo);
}
