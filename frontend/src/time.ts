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

/** Shift an ISO local calendar date without converting through a timezone instant. */
export function shiftIsoDate(localDay: string, days: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(localDay);
  if (match === null) throw new Error("localDay must be an ISO calendar date");
  const [, year, month, day] = match;
  if (year === undefined || month === undefined || day === undefined) {
    throw new Error("localDay must be an ISO calendar date");
  }
  const shifted = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day) + days));
  return shifted.toISOString().slice(0, 10);
}

export function localDateTime(now: Date, timezone: string): string {
  const parts = partsInTimezone(now, timezone);
  return `${requiredPart(parts, "year")}-${requiredPart(parts, "month")}-${requiredPart(parts, "day")}T${requiredPart(parts, "hour")}:${requiredPart(parts, "minute")}:${requiredPart(parts, "second")}`;
}

/** Human-facing abbreviation at a real instant; canonical values remain IANA IDs. */
export function timezoneAbbreviation(timezone: string, instant: Date | string = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    timeZoneName: "short",
  }).formatToParts(typeof instant === "string" ? new Date(instant) : instant);
  const abbreviation = parts.find(({ type }) => type === "timeZoneName")?.value;
  if (abbreviation === undefined || abbreviation === "") throw new Error("Intl.DateTimeFormat omitted timeZoneName");
  return abbreviation;
}

export function timezoneAbbreviationForLocalDate(timezone: string, localDay: string): string {
  const desiredWallTime = Date.parse(`${localDay}T12:00:00Z`);
  if (!Number.isFinite(desiredWallTime)) throw new Error("localDay must be an ISO calendar date");
  let instant = new Date(desiredWallTime);
  // Resolve local noon without assuming the zone's UTC offset. Two passes cover
  // large offsets and a transition between the initial guess and resolved instant.
  for (let pass = 0; pass < 2; pass += 1) {
    const parts = partsInTimezone(instant, timezone);
    const representedWallTime = Date.UTC(
      Number(requiredPart(parts, "year")),
      Number(requiredPart(parts, "month")) - 1,
      Number(requiredPart(parts, "day")),
      Number(requiredPart(parts, "hour")),
      Number(requiredPart(parts, "minute")),
      Number(requiredPart(parts, "second")),
    );
    instant = new Date(instant.getTime() + desiredWallTime - representedWallTime);
  }
  return timezoneAbbreviation(timezone, instant);
}

/** Format a timestamp that represents a real instant in the owner's timezone. */
export function formatZonedDateTime(value: string, timezone: string): string {
  const instant = new Date(value);
  if (!Number.isFinite(instant.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(instant);
}

/**
 * Format a stored wall-clock value without assigning a timezone it never had.
 * UTC is used only to make Intl preserve the recorded calendar/time fields.
 */
export function formatUnzonedDateTime(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/.exec(value);
  if (match === null) return value;
  const [, year, month, day, hour, minute] = match;
  const recorded = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute)));
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(recorded);
}
