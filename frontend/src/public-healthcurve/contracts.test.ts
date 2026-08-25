import { describe, expect, it } from "vitest";

import { parseManifest, PUBLIC_SCHEMA_VERSION } from "./contracts";

describe("public HealthCurve manifest", () => {
  it("accepts an ordered completed-date index", () => {
    expect(parseManifest({
      schema_version: PUBLIC_SCHEMA_VERSION,
      timezone: "America/New_York",
      newest_date: "2026-08-23",
      dates: ["2026-08-22", "2026-08-23"],
    }).newest_date).toBe("2026-08-23");
  });

  it("rejects duplicate, unordered, and mismatched newest dates", () => {
    expect(() => { parseManifest({
      schema_version: PUBLIC_SCHEMA_VERSION,
      timezone: "America/New_York",
      newest_date: "2026-08-22",
      dates: ["2026-08-23", "2026-08-22", "2026-08-22"],
    }); }).toThrow("validation");
  });
});
