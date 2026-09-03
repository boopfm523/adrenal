export type NumericDisplayValue = string | number | null | undefined;

const UNIT_LABELS: Readonly<Record<string, string>> = {
  bpm: "bpm",
  garmin_score: "score",
  kg: "kg",
  lb: "lb",
  mcg: "mcg",
  mg: "mg",
  mi: "mi",
  ml: "mL",
  mmhg: "mmHg",
  steps: "steps",
  tablet: "tablet",
  tablets: "tablets",
};

function expandScientificNotation(value: string): string {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?[eE]([+-]?\d+)$/.exec(value);
  if (match === null) return value;
  const sign = match[1] ?? "";
  const integer = match[2] ?? "0";
  const fraction = match[3] ?? "";
  const exponent = Number(match[4]);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1_000) return value;
  const digits = `${integer}${fraction}`;
  const decimalIndex = integer.length + exponent;
  if (decimalIndex <= 0) return `${sign}0.${"0".repeat(-decimalIndex)}${digits}`;
  if (decimalIndex >= digits.length) return `${sign}${digits}${"0".repeat(decimalIndex - digits.length)}`;
  return `${sign}${digits.slice(0, decimalIndex)}.${digits.slice(decimalIndex)}`;
}

/**
 * Format an exact decimal for human-facing en-US display without converting API
 * decimal strings through IEEE-754. Only insignificant trailing fractional zeroes
 * are removed; every remaining digit is preserved.
 */
export function formatDecimal(value: NumericDisplayValue, missing = "Unavailable"): string {
  if (value == null) return missing;
  if (typeof value === "number" && !Number.isFinite(value)) return missing;
  const raw = expandScientificNotation(String(value).trim());
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(raw);
  if (match === null) return raw;
  let integer = (match[2] ?? "0").replace(/^0+(?=\d)/, "");
  const fraction = (match[3] ?? "").replace(/0+$/, "");
  const isZero = /^0+$/.test(integer) && fraction === "";
  const sign = isZero ? "" : match[1] === "-" ? "-" : "";
  integer = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${integer}${fraction === "" ? "" : `.${fraction}`}`;
}

/**
 * Round an exact decimal string for compact summaries without routing API
 * decimals through IEEE-754. Unlike formatDecimal, this intentionally limits
 * display precision; stored and calculated values remain unchanged.
 */
export function formatRoundedDecimal(
  value: NumericDisplayValue,
  maximumFractionDigits: number,
  missing = "Unavailable",
): string {
  if (value == null) return missing;
  if (typeof value === "number" && !Number.isFinite(value)) return missing;
  if (!Number.isInteger(maximumFractionDigits) || maximumFractionDigits < 0) return formatDecimal(value, missing);

  const raw = expandScientificNotation(String(value).trim());
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(raw);
  if (match === null) return raw;

  const integer = (match[2] ?? "0").replace(/^0+(?=\d)/, "");
  const fraction = match[3] ?? "";
  const keptFraction = fraction.padEnd(maximumFractionDigits, "0").slice(0, maximumFractionDigits);
  const roundUp = (fraction[maximumFractionDigits] ?? "0") >= "5";
  let scaled = BigInt(`${integer}${keptFraction}`);
  if (roundUp) scaled += 1n;

  const digits = scaled.toString().padStart(maximumFractionDigits + 1, "0");
  const wholeDigits = maximumFractionDigits === 0 ? digits : digits.slice(0, -maximumFractionDigits);
  const whole = wholeDigits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const roundedFraction = maximumFractionDigits === 0 ? "" : digits.slice(-maximumFractionDigits).replace(/0+$/, "");
  const sign = scaled === 0n ? "" : match[1] === "-" ? "-" : "";
  return `${sign}${whole}${roundedFraction === "" ? "" : `.${roundedFraction}`}`;
}

/**
 * Format recorded miles with the two fractional digits used by activity views.
 * Rounding is performed on the decimal string so API values never pass through
 * IEEE-754 before presentation.
 */
export function formatDistanceMiles(value: NumericDisplayValue, missing = "Unavailable"): string {
  if (value == null) return missing;
  if (typeof value === "number" && !Number.isFinite(value)) return missing;
  const raw = expandScientificNotation(String(value).trim());
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(raw);
  if (match === null) return raw;

  const integer = (match[2] ?? "0").replace(/^0+(?=\d)/, "");
  const fraction = match[3] ?? "";
  const keptFraction = fraction.padEnd(2, "0").slice(0, 2);
  const roundUp = (fraction[2] ?? "0") >= "5";
  let hundredths = BigInt(`${integer}${keptFraction}`);
  if (roundUp) hundredths += 1n;

  const digits = hundredths.toString().padStart(3, "0");
  const whole = digits.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const roundedFraction = digits.slice(-2);
  const sign = hundredths === 0n ? "" : match[1] === "-" ? "-" : "";
  return `${sign}${whole}.${roundedFraction}`;
}

export function humanizeUnit(unit: string | null | undefined): string {
  if (unit == null || unit.trim() === "") return "unit not recorded";
  const normalized = unit.trim();
  return UNIT_LABELS[normalized.toLocaleLowerCase("en-US")] ?? normalized.replaceAll("_", " ");
}

export function formatMeasurement(value: NumericDisplayValue, unit: string | null | undefined, missing = "Unavailable"): string {
  if (value == null) return missing;
  return `${formatDecimal(value, missing)} ${humanizeUnit(unit)}`;
}

export function humanizeSource(source: string): string {
  const words = source.replaceAll("_", " ");
  return words.length === 0 ? words : `${words[0]?.toLocaleUpperCase("en-US") ?? ""}${words.slice(1)}`;
}

export function garminMetricLabel(metricType: string | null | undefined): string {
  const labels: Readonly<Record<string, string>> = {
    resting_heart_rate: "Resting heart rate",
    steps: "Steps",
    stress: "Stress",
  };
  if (metricType == null) return "Daily observation";
  return labels[metricType] ?? humanizeSource(metricType);
}

export function formatGarminDailyValue(metricType: string | null | undefined, value: NumericDisplayValue, unit: string | null | undefined): string {
  if (value == null) return "Unavailable";
  if (metricType === "stress") return `Stress: ${formatDecimal(value)}`;
  if (metricType === "resting_heart_rate") return `${formatDecimal(value)} bpm`;
  if (metricType === "steps") return `${formatDecimal(value)} steps`;
  return formatMeasurement(value, unit);
}

export function formatQuantitativeText(value: string): string {
  return value.replace(
    /(-?\d+(?:\.\d+)?)\s+(breaths\/min|mg|mcg|ml|mL|tablets?|lb|kg|mmHg|bpm|steps|mi|ms)\b/g,
    (_match, number: string, unit: string) => `${unit === "mi" ? formatDistanceMiles(number) : formatDecimal(number)} ${humanizeUnit(unit)}`,
  );
}

/** Format an immutable snapshot for preview only; the snapshot object is untouched. */
export function formatPreviewJson(value: unknown): string {
  return formatQuantitativeText(JSON.stringify(value, null, 2))
    .replace(/"(-?\d+\.\d+)"/g, (_match, number: string) => `"${formatDecimal(number)}"`)
    .replaceAll('"garmin_score"', '"score"');
}
