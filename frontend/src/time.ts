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
