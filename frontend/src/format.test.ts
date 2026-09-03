import {
  formatDecimal,
  formatDistanceMiles,
  formatGarminDailyValue,
  formatMeasurement,
  formatPreviewJson,
  formatQuantitativeText,
  formatRoundedDecimal,
  garminMetricLabel,
  humanizeUnit,
} from "./format";

describe("health value presentation formatting", () => {
  it.each([
    ["0.0000", "0"],
    ["-0.0000", "0"],
    ["-1234.5000", "-1,234.5"],
    ["0.00012500", "0.000125"],
    ["6099.0000", "6,099"],
    ["1234567890.0100", "1,234,567,890.01"],
    ["83.1000", "83.1"],
    ["182", "182"],
    [1e-7, "0.0000001"],
  ])("formats %s without discarding meaningful precision", (input, expected) => {
    expect(formatDecimal(input)).toBe(expected);
  });

  it("uses an explicit missing label and leaves nonnumeric qualitative text intact", () => {
    expect(formatDecimal(null)).toBe("Unavailable");
    expect(formatDecimal(undefined, "Not recorded")).toBe("Not recorded");
    expect(formatDecimal("<5")).toBe("<5");
    expect(formatDecimal("1e1001")).toBe("1e1001");
  });

  it.each([
    ["244.817090855", 1, "244.8"],
    ["96.6667", 0, "97"],
    ["57.1964", 1, "57.2"],
    ["-0.6148", 1, "-0.6"],
    ["2.0000", 1, "2"],
    ["999.96", 1, "1,000"],
    ["-0.04", 1, "0"],
  ])("rounds summary value %s to at most %i fractional digits", (input, precision, expected) => {
    expect(formatRoundedDecimal(input, precision)).toBe(expected);
  });

  it("uses readable deterministic unit and Garmin labels", () => {
    expect(humanizeUnit("garmin_score")).toBe("score");
    expect(humanizeUnit("ml")).toBe("mL");
    expect(humanizeUnit("custom_provider_unit")).toBe("custom provider unit");
    expect(formatMeasurement("15.0000", "mg")).toBe("15 mg");
    expect(garminMetricLabel("resting_heart_rate")).toBe("Resting heart rate");
    expect(formatGarminDailyValue("stress", "28.0000", "garmin_score")).toBe("Stress: 28");
    expect(formatGarminDailyValue("steps", "6099.0000", "steps")).toBe("6,099 steps");
    expect(formatGarminDailyValue("resting_heart_rate", "53.0000", "bpm")).toBe("53 bpm");
  });

  it.each([
    ["1.2555", "1.26"],
    ["3.1000", "3.10"],
    ["0", "0.00"],
    ["999.9999", "1,000.00"],
    ["-0.0049", "0.00"],
    ["-0.0050", "-0.01"],
  ])("formats activity distance %s with exactly two decimal places", (input, expected) => {
    expect(formatDistanceMiles(input)).toBe(expected);
  });

  it("formats quantitative summaries and previews without mutating their source values", () => {
    const snapshot = { amount: "15.0000", unit: "mg", stress: "28.0000", stress_unit: "garmin_score" };
    expect(formatQuantitativeText("Synthetic medicine 10.0000 mg")).toBe("Synthetic medicine 10 mg");
    expect(formatQuantitativeText("Average sleeping respiration: 14.0000 breaths/min")).toBe("Average sleeping respiration: 14 breaths/min");
    expect(formatQuantitativeText("Nightly average HRV: 35.5000 ms")).toBe("Nightly average HRV: 35.5 ms");
    expect(formatQuantitativeText("Activity: Walking; 1.2555 mi")).toBe("Activity: Walking; 1.26 mi");
    expect(formatPreviewJson(snapshot)).toContain('"amount": "15"');
    expect(formatPreviewJson(snapshot)).toContain('"stress_unit": "score"');
    expect(snapshot).toEqual({ amount: "15.0000", unit: "mg", stress: "28.0000", stress_unit: "garmin_score" });
  });
});
