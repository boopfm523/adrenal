function partsInTimezone(now: Date, timezone: string): Record<string, string> {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  return Object.fromEntries(parts.map(({ type, value }) => [type, value]));
}

function requiredPart(parts: Record<string, string>, name: string): string {
  const value = parts[name];
  if (value === undefined) throw new Error(`Intl.DateTimeFormat omitted ${name}`);
  return value;
}

export function localDate(now: Date, timezone: string): string {
  const parts = partsInTimezone(now, timezone);
  return `${requiredPart(parts, "year")}-${requiredPart(parts, "month")}-${requiredPart(parts, "day")}`;
}

export function localDateTime(now: Date, timezone: string): string {
  const parts = partsInTimezone(now, timezone);
  return `${requiredPart(parts, "year")}-${requiredPart(parts, "month")}-${requiredPart(parts, "day")}T${requiredPart(parts, "hour")}:${requiredPart(parts, "minute")}:${requiredPart(parts, "second")}`;
}
