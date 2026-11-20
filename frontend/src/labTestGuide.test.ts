import { LAB_CATEGORIES, categorize, guideFor } from "./labTestGuide";

describe("lab test guide", () => {
  it.each([
    ["FEV1 - Pre % Predicted", "Pulmonary function tests (PFTs)"],
    ["RV/TLC (Pleth) - Pre Z-Score", "Pulmonary function tests (PFTs)"],
    ["DLCO, Corrected - Lower Predicted", "Pulmonary function tests (PFTs)"],
    ["IC (N2) - Actual, Pre", "Pulmonary function tests (PFTs)"],
    ["ICU?", "Notes and order details"],
    ["QTCF Interval", "Heart: ECG, echo, and stress test"],
    ["LVOT Peak Velocity", "Heart: ECG, echo, and stress test"],
    ["pO2., Art, POCT", "Arterial blood gases"],
    ["Glucose (urine)", "Urine"],
    ["Cortisol, Free (urine)", "Adrenal and pituitary"],
    ["ACTH", "Adrenal and pituitary"],
    ["Free T4", "Thyroid"],
    ["Alkaline Phosphatase", "Liver and proteins"],
    ["Phosphorus", "Electrolytes and kidney"],
    ["Hemoglobin  A1C", "Glucose"],
    ["Mean corpuscular volume", "Blood counts"],
    ["Sex Hormone Binding Globulin", "Sex hormones"],
    ["Appearance (fluid)", "Other"],
  ])("files %s under %s", (name, category) => {
    expect(categorize(name)).toBe(category);
  });

  it("keeps categories in a fixed, unique order", () => {
    expect(new Set(LAB_CATEGORIES).size).toBe(LAB_CATEGORIES.length);
    expect(LAB_CATEGORIES.at(-1)).toBe("Other");
  });

  it("describes a test by name, including registry display names", () => {
    expect(guideFor("Hemoglobin")?.title).toBe("Hemoglobin");
    expect(guideFor("Bicarbonate / total CO2")?.title).toBe("Bicarbonate (CO2)");
    expect(guideFor("Adrenocorticotropic Hormone")?.title).toBe("ACTH (adrenocorticotropic hormone)");
    expect(guideFor("Cortisol, Saliva Sample 1")?.title).toBe("Salivary cortisol");
  });

  it("never describes a urine result with a blood-test entry", () => {
    expect(guideFor("Bilirubin (urine)")?.title).toBe("Urinalysis");
    expect(guideFor("Glucose (urine)")?.title).toBe("Glucose in urine");
    expect(guideFor("Culture (urine)")?.title).toBe("Urine culture");
    expect(guideFor("Bilirubin, Total")?.title).toBe("Bilirubin");
  });

  it("does not mistake a blood-gas oxygen saturation for iron saturation", () => {
    expect(guideFor("O2 Saturation, Art, POCT")?.title).toBe("Arterial blood gas");
    expect(guideFor("% Saturation")?.title).toBe("Iron studies");
  });

  it("explains a PFT measurement, its variant, and its method", () => {
    const guide = guideFor("FEV1/FVC (Pleth) - Pre % Predicted");
    expect(guide?.measures).toMatch(/^FEV1\/FVC:/u);
    expect(guide?.notes).toMatch(/% Predicted/u);
    expect(guide?.notes).toMatch(/body box/u);
    expect(guideFor("TLC (N2) - Pre Z-Score")?.notes).toMatch(/standard deviations.*nitrogen washout/su);
  });

  it("links only to MedlinePlus over HTTPS", () => {
    const names = ["Sodium", "Cortisol", "TSH", "FEV1 - Actual, Pre", "QT Interval", "Protein (urine)", "Ferritin", "Hemoglobin"];
    for (const name of names) {
      const url = guideFor(name)?.source?.url ?? "";
      expect(url).toMatch(/^https:\/\/medlineplus\.gov\/(lab-tests|ency\/article)\//u);
    }
  });

  it("returns nothing rather than guessing for an unknown test", () => {
    expect(guideFor("Appearance (fluid)")).toBeNull();
  });
});
