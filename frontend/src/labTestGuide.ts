/**
 * Plain-language, owner-facing descriptions of laboratory and clinical tests.
 *
 * Deterministic and versioned: no model writes or rewrites this text. Every entry is
 * general education about what a test measures and why it is commonly ordered. None
 * of it interprets the owner's values, sets a target, or recommends treatment. Source
 * links point only to pages whose titles were checked on MedlinePlus (U.S. National
 * Library of Medicine) on 2026-11-20.
 */

export const LAB_TEST_GUIDE_VERSION = "hc-lab-test-guide-v1";

export const LAB_CATEGORIES = [
  "Adrenal and pituitary",
  "Thyroid",
  "Sex hormones",
  "Electrolytes and kidney",
  "Glucose",
  "Liver and proteins",
  "Muscle and tissue enzymes",
  "Lipids",
  "Blood counts",
  "Iron and vitamins",
  "Clotting",
  "Inflammation and infection",
  "Arterial blood gases",
  "Urine",
  "Pulmonary function tests (PFTs)",
  "Heart: ECG, echo, and stress test",
  "Tumor markers",
  "Notes and order details",
  "Other",
] as const;

export type LabCategory = (typeof LAB_CATEGORIES)[number];

export interface LabTestGuide {
  title: string;
  measures: string;
  why: string;
  notes?: string;
  source?: { label: string; url: string };
}

const MP = "https://medlineplus.gov/lab-tests/";
const ENCY = "https://medlineplus.gov/ency/article/";

function mp(path: string, label: string): { label: string; url: string } {
  return { label: `MedlinePlus: ${label}`, url: `${MP}${path}/` };
}

function ency(article: string, label: string): { label: string; url: string } {
  return { label: `MedlinePlus: ${label}`, url: `${ENCY}${article}.htm` };
}

/** Lower-case, punctuation-free form used for matching names. */
export function testKey(name: string): string {
  return name.toLocaleLowerCase().replace(/[^a-z0-9%]+/gu, " ").trim();
}

// ---------------------------------------------------------------------------
// Categories
// ---------------------------------------------------------------------------

const PFT_MEASURE = /^(fev[0-9.]*|fvc|fef[0-9-]*|fif[0-9]*|fet|pefr|pifr|dlco|kco|tlc|rv|erv|frc|ic|vc|svc|raw|sgaw|lci)\b/iu;

const CATEGORY_RULES: readonly (readonly [LabCategory, RegExp])[] = [
  // Urine and saliva cortisol belong with the adrenal tests, not the urinalysis.
  ["Adrenal and pituitary", /cortisol/iu],
  ["Urine", /\(urine\)\s*$/iu],
  ["Pulmonary function tests (PFTs)", PFT_MEASURE],
  ["Pulmonary function tests (PFTs)", /^z-score \(male\)$/iu],
  ["Arterial blood gases", /\bart\b.*\bpoct\b/iu],
  ["Heart: ECG, echo, and stress test", /\b(axis|interval|qtc|qrs|ventricular rate|atrial rate|q-t|p-r)\b/iu],
  ["Heart: ECG, echo, and stress test", /(aortic root|lvot|tr peak|av peak|ivc diameter|ivs |lvpw|lv (systolic|diastolic)|la systolic|rvsp|ra pressure)/iu],
  ["Heart: ECG, echo, and stress test", /^max (heart rate|predicted heart rate|systolic bp|diastolic bp)$/iu],
  ["Notes and order details", /^(comment|source|draw (date|time) \d|hospitalized\?|icu\?|healthcare worker\?|congregate care setting\?|patient symptomatic\?|date of onset|lavender tube.*|morphology comment|interpretation, .*|apt+t\. comment)$/iu],
  ["Adrenal and pituitary", /(cortisol|acth|adrenocorticotropic|aldosterone|renin|dhea|metanephrine|normetanephrine|mn ?\+ ?nmn|dexamethasone|growth hormone|igf|insulin-like growth|prolactin|follicle stim|fsh|luteinizing)/iu],
  ["Thyroid", /(^tsh$|thyroid|thyroxine|\bt4\b|\bt3\b|triiodothyronine)/iu],
  ["Sex hormones", /(testosterone|sex hormone binding)/iu],
  ["Tumor markers", /(ca 125|cancer antigen|prostate spec)/iu],
  ["Glucose", /(glucose|a1c)/iu],
  ["Lipids", /(cholesterol|chol\/hdl|\bhdl\b|\bldl\b|triglycerides)/iu],
  ["Iron and vitamins", /(^iron$|ferritin|transferrin|% saturation|vitamin|folate|erythropoietin)/iu],
  ["Clotting", /(^pt$|^inr$|aptt|fibrinogen|prothrombin)/iu],
  ["Inflammation and infection", /(crp|c-reactive|sedimentation|covid|sars|flu |rsv|culture|carbapenemase)/iu],
  ["Muscle and tissue enzymes", /(^ck\b|ck-mb|creatine kinase|^ldh$|lactate dehydrogenase)/iu],
  ["Electrolytes and kidney", /(sodium|potassium|chloride|bicarbonate|carbon dioxide|^co2|anion gap|^bun\b|blood urea|creatinine|gfr|cystatin|calcium|magnesium|^phosph|uric acid|osmolality)/iu],
  ["Liver and proteins", /(^alt$|^ast$|ast\/alt|alanine|aspartate|alkaline phosphatase|bilirubin|albumin|globulin|total protein|protein, total)/iu],
  ["Blood counts", /(white blood|red blood|wbc|rbc|hemoglobin|hematocrit|\bmcv\b|\bmch\b|mchc|\brdw\b|platelet|\bmpv\b|neutro|lymph|mono|eosino|baso|band|immature|nucleated|retic|anisocytosis|microcytes|ovalocytes|polychromasia|tear drop|giant thrombocytes|myelocytes|\banc\b|mean corpuscular|red cell distribution|mean platelet)/iu],
];

export function categorize(name: string): LabCategory {
  for (const [category, pattern] of CATEGORY_RULES) {
    if (pattern.test(name.trim())) return category;
  }
  return "Other";
}

// ---------------------------------------------------------------------------
// Individual tests
// ---------------------------------------------------------------------------

interface Entry extends LabTestGuide {
  match: readonly RegExp[];
  /** Applies to urine results; other entries never describe a urine result. */
  urine?: true;
}

const ENTRIES: readonly Entry[] = [
  // Adrenal and pituitary
  {
    match: [/^cortisol$/u, /^cortisol serum$/u],
    title: "Cortisol",
    measures: "Cortisol is the main stress hormone made by the adrenal glands. It helps control blood pressure, blood sugar, and the body's response to illness and stress.",
    why: "Measured to check whether the adrenal glands make too little or too much cortisol, and to follow conditions such as Cushing's syndrome and adrenal insufficiency.",
    notes: "Levels normally change across the day, highest in the early morning. Hydrocortisone is cortisol, so a level drawn after a dose largely reflects the medication. Prednisone and prednisolone can be picked up by many cortisol tests, while dexamethasone usually is not.",
    source: mp("cortisol-test", "Cortisol Test"),
  },
  {
    match: [/^cortisol saliva/u],
    title: "Salivary cortisol",
    measures: "Cortisol measured in saliva, usually collected at home late at night.",
    why: "Cortisol is normally lowest around bedtime. A late-night saliva sample is one of the screening tests for Cushing's syndrome; two samples on different nights are often collected.",
    notes: "Steroid creams, inhalers, or tablets, and blood in the saliva, can affect the result.",
    source: mp("cortisol-test", "Cortisol Test"),
  },
  {
    match: [/^cortisol free urine$/u],
    title: "Urine free cortisol",
    measures: "Cortisol that passes into the urine, usually measured over a full 24-hour collection.",
    why: "A 24-hour urine free cortisol reflects the body's total cortisol output over a day and is used to screen for and follow Cushing's syndrome.",
    notes: "An incomplete collection or steroid medication can change the result.",
    source: mp("cortisol-test", "Cortisol Test"),
  },
  {
    match: [/^acth$/u, /^adrenocorticotropic hormone$/u],
    title: "ACTH (adrenocorticotropic hormone)",
    measures: "ACTH is made by the pituitary gland in the brain. It signals the adrenal glands to make cortisol, and cortisol in turn tells the pituitary to make less ACTH.",
    why: "Measured with cortisol to find where a cortisol problem starts: in the pituitary (such as an ACTH-secreting pituitary adenoma in Cushing's disease), in the adrenal glands, or elsewhere. After both adrenal glands are removed, ACTH is often followed over time because the pituitary no longer receives cortisol feedback from the adrenals.",
    notes: "ACTH is highest in the morning and is suppressed by glucocorticoid medication, so timing relative to doses matters. Samples must be kept cold and processed quickly.",
    source: mp("adrenocorticotropic-hormone-acth", "Adrenocorticotropic Hormone (ACTH)"),
  },
  {
    match: [/^aldosterone/u],
    title: "Aldosterone",
    measures: "Aldosterone is an adrenal hormone that helps the kidneys keep sodium and remove potassium, which affects blood pressure and fluid balance.",
    why: "Usually measured with renin to check for too much aldosterone (a cause of high blood pressure) or too little (as in adrenal insufficiency).",
    notes: "Posture, salt intake, and several blood pressure medicines affect the result.",
    source: mp("aldosterone-test", "Aldosterone Test"),
  },
  {
    match: [/renin/u],
    title: "Renin",
    measures: "Renin is an enzyme from the kidneys that starts the chain of signals leading to aldosterone release.",
    why: "Read together with aldosterone. When the adrenal glands cannot make aldosterone, renin is one of the values clinicians use to judge mineralocorticoid (fludrocortisone) replacement.",
    source: mp("renin-test", "Renin Test"),
  },
  {
    match: [/^dhea/u],
    title: "DHEA sulfate",
    measures: "DHEA sulfate is a hormone made mostly by the adrenal glands that the body converts into sex hormones.",
    why: "Used to assess adrenal function and adrenal causes of excess androgen. Levels are low when the adrenal glands are absent or not working.",
    source: mp("dhea-sulfate-test", "DHEA Sulfate Test"),
  },
  {
    match: [/metanephrine/u, /mn nmn/u],
    title: "Metanephrines",
    measures: "Metanephrine and normetanephrine are breakdown products of adrenaline and noradrenaline, which are made in the inner part of the adrenal gland.",
    why: "Measured to look for pheochromocytoma and related tumors that make too much adrenaline or noradrenaline, including during the work-up of an adrenal nodule.",
    source: mp("catecholamine-tests", "Catecholamine Tests"),
  },
  {
    match: [/^dexamethasone$/u],
    title: "Dexamethasone level",
    measures: "The amount of dexamethasone, a synthetic steroid, in the blood.",
    why: "Usually drawn during a dexamethasone suppression test to confirm the tablet was taken and absorbed, so that the cortisol result from the same test can be trusted.",
    source: mp("cortisol-test", "Cortisol Test"),
  },
  {
    match: [/^growth hormone$/u],
    title: "Growth hormone",
    measures: "A pituitary hormone that affects growth, body composition, and metabolism.",
    why: "Checked when pituitary disease or surgery may have changed how the pituitary works. Because it is released in pulses, it is usually read together with IGF-1.",
    source: mp("growth-hormone-tests", "Growth Hormone Tests"),
  },
  {
    match: [/igf/u, /insulin like growth factor/u],
    title: "IGF-1",
    measures: "IGF-1 is made mainly by the liver in response to growth hormone and stays steady in the blood through the day.",
    why: "Used as a steadier marker of growth hormone activity, for example after pituitary surgery.",
    source: mp("igf-1-insulin-like-growth-factor-1-test", "IGF-1 Test"),
  },
  {
    match: [/^prolactin/u],
    title: "Prolactin",
    measures: "A pituitary hormone best known for its role in milk production; it is present in everyone.",
    why: "Checked when pituitary tumors or pituitary surgery are being evaluated, since prolactin can rise or fall with pituitary changes and some medications.",
    source: mp("prolactin-levels", "Prolactin Levels"),
  },
  {
    match: [/follicle stim/u],
    title: "FSH (follicle-stimulating hormone)",
    measures: "A pituitary hormone that controls the testes or ovaries.",
    why: "Measured with LH and sex hormones to check whether the pituitary is signalling the reproductive glands normally.",
    source: mp("follicle-stimulating-hormone-fsh-levels-test", "FSH Levels Test"),
  },
  {
    match: [/luteinizing/u],
    title: "LH (luteinizing hormone)",
    measures: "A pituitary hormone that tells the testes to make testosterone (and regulates ovulation in women).",
    why: "Read with FSH and testosterone to tell a pituitary cause of low sex hormones from a problem in the testes or ovaries.",
    source: mp("luteinizing-hormone-lh-levels-test", "LH Levels Test"),
  },
  // Thyroid
  {
    match: [/^tsh$/u],
    title: "TSH (thyroid-stimulating hormone)",
    measures: "A pituitary hormone that tells the thyroid gland how much thyroid hormone to make.",
    why: "The usual first test of thyroid function. After pituitary disease or surgery, TSH can be unreliable on its own, so free T4 is often read alongside it.",
    source: mp("tsh-thyroid-stimulating-hormone-test", "TSH Test"),
  },
  {
    match: [/^free t4$/u, /^thyroxine free$/u, /^thyroxine$/u],
    title: "T4 (thyroxine)",
    measures: "The main hormone made by the thyroid. \"Free\" T4 is the portion not bound to proteins and available to the body.",
    why: "Used with TSH to check for an underactive or overactive thyroid, and to follow thyroid hormone replacement.",
    source: mp("thyroxine-t4-test", "Thyroxine (T4) Test"),
  },
  {
    match: [/t3/u, /triiodothyronine/u],
    title: "T3 (triiodothyronine)",
    measures: "The more active thyroid hormone, made partly by the thyroid and partly from T4 in the body's tissues.",
    why: "Usually added when TSH and T4 do not explain symptoms, especially when an overactive thyroid is suspected.",
    source: mp("triiodothyronine-t3-tests", "T3 Tests"),
  },
  // Sex hormones and tumor markers
  {
    match: [/testosterone/u],
    title: "Testosterone",
    measures: "The main male sex hormone. Tests may report total, free, or bioavailable testosterone.",
    why: "Checked for symptoms of low or high testosterone and, after pituitary disease, to see whether the pituitary-testis signal is intact.",
    notes: "Levels are highest in the morning.",
    source: mp("testosterone-levels-test", "Testosterone Levels Test"),
  },
  {
    match: [/^sex hormone binding globulin$/u],
    title: "SHBG (sex hormone-binding globulin)",
    measures: "A protein that carries testosterone and estrogen in the blood.",
    why: "Used to calculate how much testosterone is free and available, since SHBG changes with age, weight, thyroid function, and medications.",
    source: mp("testosterone-levels-test", "Testosterone Levels Test"),
  },
  {
    match: [/prostate spec/u],
    title: "PSA (prostate-specific antigen)",
    measures: "A protein made by the prostate gland.",
    why: "Used to screen for and follow prostate conditions, including cancer. Many non-cancer causes can raise it.",
    source: mp("prostate-specific-antigen-psa-test", "PSA Test"),
  },
  {
    match: [/ca 125/u],
    title: "CA-125",
    measures: "A protein released by some cells, including some cancer cells and cells lining the chest and abdomen.",
    why: "Mostly used to follow certain cancers. Inflammation or fluid in the chest or abdomen can also raise it.",
    source: mp("ca-125-blood-test-ovarian-cancer", "CA-125 Blood Test"),
  },
  // Electrolytes and kidney
  {
    match: [/^sodium/u],
    title: "Sodium",
    measures: "Sodium is an electrolyte that controls fluid balance and nerve and muscle function.",
    why: "Part of routine chemistry panels. It is watched closely in adrenal insufficiency, because cortisol and aldosterone both help the body regulate sodium and water.",
    source: mp("sodium-blood-test", "Sodium Blood Test"),
  },
  {
    match: [/^potassium/u],
    title: "Potassium",
    measures: "Potassium is an electrolyte essential for heart rhythm and muscle function.",
    why: "Part of routine chemistry panels. Aldosterone helps the kidneys remove potassium, so it is one of the values used to monitor mineralocorticoid (fludrocortisone) replacement when the adrenal glands cannot make aldosterone.",
    source: mp("potassium-blood-test", "Potassium Blood Test"),
  },
  {
    match: [/^chloride$/u],
    title: "Chloride",
    measures: "An electrolyte that moves with sodium and helps keep fluid and acid-base balance.",
    why: "Part of routine chemistry panels, usually read together with sodium and bicarbonate.",
    source: mp("chloride-blood-test", "Chloride Blood Test"),
  },
  {
    match: [/^bicarbonate$/u, /^bicarbonate total co2$/u, /^co2$/u, /carbon dioxide/u],
    title: "Bicarbonate (CO2)",
    measures: "The amount of bicarbonate, the body's main buffer against acid, in the blood.",
    why: "Shows whether the blood is becoming too acidic or too alkaline, which can happen with kidney, lung, or metabolic problems.",
    source: mp("carbon-dioxide-co2-in-blood", "Carbon Dioxide (CO2) in Blood"),
  },
  {
    match: [/^anion gap$/u],
    title: "Anion gap",
    measures: "A calculated value from sodium, chloride, and bicarbonate, not a separate measurement.",
    why: "Helps explain the cause when the blood is too acidic.",
    source: mp("anion-gap-blood-test", "Anion Gap Blood Test"),
  },
  {
    match: [/^bun$/u, /blood urea nitrogen/u],
    title: "BUN (blood urea nitrogen)",
    measures: "Urea is a waste product from protein breakdown that the kidneys clear.",
    why: "A basic kidney test. It also rises with dehydration, bleeding in the gut, high protein intake, and steroid medicines.",
    source: mp("bun-blood-urea-nitrogen", "BUN"),
  },
  {
    match: [/bun creatinine ratio/u],
    title: "BUN/creatinine ratio",
    measures: "A calculated ratio of BUN to creatinine.",
    why: "Helps suggest whether a change in kidney numbers is more likely from fluid status or from the kidneys themselves.",
    source: mp("bun-blood-urea-nitrogen", "BUN"),
  },
  {
    match: [/^creatinine$/u, /^creatinine venous$/u],
    title: "Creatinine",
    measures: "A waste product from normal muscle use that the kidneys filter out.",
    why: "The standard blood marker of kidney function, used to calculate eGFR. It also depends on muscle mass.",
    source: mp("creatinine-test", "Creatinine Test"),
  },
  {
    match: [/gfr/u],
    title: "eGFR (estimated glomerular filtration rate)",
    measures: "An estimate of how much blood the kidneys filter each minute, calculated from creatinine and/or cystatin C with age and sex.",
    why: "The main number used to stage kidney function. Different equations (CKD-EPI, MDRD, creatinine or cystatin C) give slightly different estimates, so they are listed separately.",
    source: mp("glomerular-filtration-rate-gfr-test", "eGFR and GFR Tests"),
  },
  {
    match: [/^cystatin c$/u],
    title: "Cystatin C",
    measures: "A small protein made by all cells and filtered by the kidneys.",
    why: "An alternative kidney marker that depends less on muscle mass than creatinine; it is used in some eGFR equations.",
    source: mp("glomerular-filtration-rate-gfr-test", "eGFR and GFR Tests"),
  },
  {
    match: [/^calcium$/u, /ionized calcium/u],
    title: "Calcium",
    measures: "The mineral that keeps bones strong and lets nerves, muscles, and the heart work.",
    why: "Part of routine panels. Total calcium depends on albumin; ionized calcium measures the active part directly.",
    source: mp("calcium-blood-test", "Calcium Blood Test"),
  },
  {
    match: [/^magnesium$/u],
    title: "Magnesium",
    measures: "A mineral needed for muscle, nerve, and heart function.",
    why: "Checked with other electrolytes, especially with abnormal potassium or calcium, or with certain medicines.",
    source: mp("magnesium-blood-test", "Magnesium Blood Test"),
  },
  {
    match: [/^phosph/u],
    title: "Phosphorus (phosphate)",
    measures: "A mineral that works with calcium in bones and is needed for energy use.",
    why: "Checked with calcium for bone, kidney, and parathyroid conditions.",
    source: mp("phosphate-in-blood", "Phosphate in Blood"),
  },
  {
    match: [/^uric acid$/u],
    title: "Uric acid",
    measures: "A waste product from breaking down purines, found in many foods and in the body's own cells.",
    why: "Checked for gout, kidney stones, and some kidney or treatment-related conditions.",
    source: mp("uric-acid-test", "Uric Acid Test"),
  },
  {
    match: [/osmolality/u],
    title: "Osmolality",
    measures: "How concentrated the blood is, set mostly by sodium, glucose, and urea.",
    why: "Used to investigate low sodium, water balance, and some poisonings.",
    source: mp("osmolality-tests", "Osmolality Tests"),
  },
  // Glucose
  {
    match: [/^glucose$/u, /^glucose capillary/u],
    title: "Glucose",
    measures: "The level of sugar in the blood.",
    why: "Screens for and follows diabetes and low blood sugar. Cortisol raises blood sugar, so steroid doses can raise glucose and too little cortisol can contribute to low glucose.",
    notes: "Capillary (finger-stick) and venous results come from different samples.",
    source: mp("blood-glucose-test", "Blood Glucose Test"),
  },
  {
    match: [/a1c/u],
    title: "Hemoglobin A1c",
    measures: "The share of hemoglobin with sugar attached, reflecting average blood sugar over about the last three months.",
    why: "Used to diagnose and follow diabetes. Anemia, blood loss, or transfusion can make it less accurate.",
    source: mp("hemoglobin-a1c-hba1c-test", "Hemoglobin A1C Test"),
  },
  // Liver and proteins
  {
    match: [/^alt$/u],
    title: "ALT (alanine aminotransferase)",
    measures: "An enzyme found mainly in the liver.",
    why: "A sensitive marker of liver cell irritation or injury, from causes such as fatty liver, medications, or infection.",
    source: mp("alt-blood-test", "ALT Blood Test"),
  },
  {
    match: [/^ast$/u],
    title: "AST (aspartate aminotransferase)",
    measures: "An enzyme found in the liver and also in muscle and the heart.",
    why: "Read with ALT to assess the liver; muscle injury or strenuous exercise can raise it too.",
    source: mp("ast-test", "AST Test"),
  },
  {
    match: [/^ast alt ratio$/u],
    title: "AST/ALT ratio",
    measures: "A calculated ratio of the two liver enzymes.",
    why: "Can help suggest the pattern of a liver problem when the enzymes are abnormal.",
    source: mp("liver-function-tests", "Liver Function Tests"),
  },
  {
    match: [/^alkaline phosphatase$/u],
    title: "Alkaline phosphatase",
    measures: "An enzyme from the liver, bile ducts, and bones.",
    why: "Raised levels can point to bile duct or bone conditions.",
    source: mp("alkaline-phosphatase", "Alkaline Phosphatase"),
  },
  {
    match: [/bilirubin/u],
    title: "Bilirubin",
    measures: "A yellow pigment made when red blood cells break down, processed by the liver. Direct bilirubin is the part the liver has already processed.",
    why: "Checks liver and bile duct function and red cell breakdown; high levels cause jaundice.",
    source: mp("bilirubin-blood-test", "Bilirubin Blood Test"),
  },
  {
    match: [/^albumin$/u],
    title: "Albumin",
    measures: "The main protein made by the liver; it keeps fluid in the blood vessels and carries hormones and medicines.",
    why: "Reflects liver function, nutrition, and protein loss through the kidneys or gut. It also affects how total calcium is read.",
    source: mp("albumin-blood-test", "Albumin Blood Test"),
  },
  {
    match: [/^globulin$/u, /albumin globulin ratio/u],
    title: "Globulin and albumin/globulin ratio",
    measures: "Globulins are blood proteins that include antibodies; the ratio compares albumin with them.",
    why: "Changes can point to liver disease, inflammation, or immune system conditions.",
    source: mp("globulin-test", "Globulin Test"),
  },
  {
    match: [/^total protein$/u, /^protein total$/u],
    title: "Total protein",
    measures: "The combined amount of albumin and globulins in the blood.",
    why: "A general marker of nutrition, liver function, and immune proteins.",
    source: mp("liver-function-tests", "Liver Function Tests"),
  },
  // Muscle and tissue enzymes
  {
    match: [/^ck$/u, /creatine kinase/u],
    title: "CK (creatine kinase)",
    measures: "An enzyme found mostly in muscles, including the heart.",
    why: "Rises after muscle injury, strenuous exercise, surgery, or with some medicines.",
    source: mp("creatine-kinase", "Creatine Kinase Test"),
  },
  {
    match: [/ck mb/u],
    title: "CK-MB",
    measures: "The form of creatine kinase found mostly in heart muscle.",
    why: "Was used to detect heart muscle injury; troponin is now preferred, but CK-MB is still reported in some panels.",
    source: mp("creatine-kinase", "Creatine Kinase Test"),
  },
  {
    match: [/^ldh$/u],
    title: "LDH (lactate dehydrogenase)",
    measures: "An enzyme present in almost every tissue.",
    why: "A general marker of tissue damage, used to follow some blood disorders, infections, and cancers.",
    source: mp("lactate-dehydrogenase-ldh-test", "LDH Test"),
  },
  // Lipids
  {
    match: [/cholesterol/u, /^hdl$/u, /^ldl/u, /triglycerides/u, /chol hdl/u, /ldl hdl/u],
    title: "Cholesterol panel",
    measures: "Fats in the blood: total, HDL (\"good\"), LDL (\"bad\"), and non-HDL cholesterol, triglycerides, and calculated ratios.",
    why: "Used to estimate heart and blood vessel risk and to guide treatment.",
    notes: "Triglycerides are affected by recent meals; LDL is often calculated rather than measured.",
    source: mp("cholesterol-levels", "Cholesterol Levels"),
  },
  // Blood counts
  {
    match: [/^white blood cell count$/u, /^wbc$/u],
    title: "White blood cell count",
    measures: "The number of white blood cells, which fight infection.",
    why: "Rises with infection, inflammation, and stress, and with glucocorticoid medicines. Low counts can follow some treatments or bone marrow problems.",
    source: mp("white-blood-count-wbc", "White Blood Count"),
  },
  {
    match: [/^red blood cell count$/u, /^rbc$/u],
    title: "Red blood cell count",
    measures: "The number of red blood cells, which carry oxygen.",
    why: "Read with hemoglobin and hematocrit to find and classify anemia.",
    source: mp("red-blood-cell-rbc-count", "RBC Count"),
  },
  {
    match: [/^hemoglobin$/u, /hemoglobin total art/u],
    title: "Hemoglobin",
    measures: "Hemoglobin is the protein in red blood cells that carries oxygen from the lungs to the rest of the body.",
    why: "The main test for anemia (too little hemoglobin) and for too many red cells. It is followed after surgery and bleeding, and with chronic illness, kidney disease, and some medicines.",
    notes: "Results can change with hydration, blood loss, transfusion, and altitude.",
    source: mp("hemoglobin-test", "Hemoglobin Test"),
  },
  {
    match: [/^hematocrit/u],
    title: "Hematocrit",
    measures: "The percentage of the blood's volume made up of red blood cells.",
    why: "Read with hemoglobin to detect anemia or too many red cells.",
    source: mp("hematocrit-test", "Hematocrit Test"),
  },
  {
    match: [/mean corpuscular volume/u, /^mcv$/u],
    title: "MCV (mean corpuscular volume)",
    measures: "The average size of red blood cells.",
    why: "Helps classify anemia, for example small cells with iron deficiency or large cells with low vitamin B12 or folate.",
    source: mp("mcv-mean-corpuscular-volume", "MCV"),
  },
  {
    match: [/mean corpuscular hemoglobin/u, /^mch$/u, /^mchc$/u],
    title: "MCH and MCHC",
    measures: "The average amount (MCH) and concentration (MCHC) of hemoglobin in each red blood cell.",
    why: "Used with MCV to classify anemia.",
    source: mp("complete-blood-count-cbc", "Complete Blood Count"),
  },
  {
    match: [/red cell distribution width/u, /^rdw$/u],
    title: "RDW (red cell distribution width)",
    measures: "How much red blood cells vary in size.",
    why: "Helps classify anemia; a high value means a mix of cell sizes.",
    source: mp("rdw-red-cell-distribution-width", "RDW"),
  },
  {
    match: [/^platelets?$/u, /platelet count/u, /platelet estimate/u, /giant thrombocytes/u],
    title: "Platelets",
    measures: "Platelets are small cell fragments that help blood clot.",
    why: "Checked for bleeding or clotting problems and with medicines or conditions that affect the bone marrow.",
    source: mp("platelet-tests", "Platelet Tests"),
  },
  {
    match: [/mean platelet volume/u, /^mpv$/u],
    title: "MPV (mean platelet volume)",
    measures: "The average size of platelets.",
    why: "Read with the platelet count; younger platelets tend to be larger.",
    source: mp("mpv-blood-test", "MPV Blood Test"),
  },
  {
    match: [/anisocytosis/u, /microcytes/u, /ovalocytes/u, /polychromasia/u, /tear drop/u, /atypical lymph/u, /variant lymph/u],
    title: "Blood smear findings",
    measures: "Descriptions of red or white cell shapes and sizes seen under the microscope.",
    why: "Adds detail when the automated count is abnormal.",
    source: mp("blood-smear", "Blood Smear"),
  },
  {
    match: [/neutro/u, /^anc/u, /lymph/u, /mono/u, /eosino/u, /baso/u, /band/u],
    title: "White cell differential",
    measures: "The share (%) and number (absolute) of each type of white blood cell: neutrophils, lymphocytes, monocytes, eosinophils, and basophils.",
    why: "Shows which kind of white cell is high or low, which helps point to infection, inflammation, allergy, or a medicine effect. Glucocorticoids typically raise neutrophils and lower lymphocytes and eosinophils.",
    source: mp("blood-differential", "Blood Differential"),
  },
  {
    match: [/immature gran/u, /myelocytes/u, /metamyelocytes/u],
    title: "Immature granulocytes",
    measures: "Young white blood cells released early from the bone marrow.",
    why: "Small numbers can appear with infection, inflammation, pregnancy, or steroid use.",
    source: mp("blood-differential", "Blood Differential"),
  },
  {
    match: [/nucleated rbc/u],
    title: "Nucleated red blood cells",
    measures: "Immature red blood cells that still have a nucleus.",
    why: "Normally absent in adult blood; they can appear when the bone marrow is under strain.",
    source: mp("complete-blood-count-cbc", "Complete Blood Count"),
  },
  {
    match: [/retic/u],
    title: "Reticulocytes",
    measures: "Young red blood cells newly released from the bone marrow, reported as a count, a percentage, an immature fraction, and their hemoglobin content.",
    why: "Shows whether the bone marrow is making red cells at the expected rate, for example during recovery from anemia or bleeding.",
    source: mp("reticulocyte-count", "Reticulocyte Count"),
  },
  {
    match: [/^hemoglobin a$/u, /^hemoglobin a2$/u, /^hemoglobin fetal$/u, /hemoglobin electro/u],
    title: "Hemoglobin electrophoresis",
    measures: "The proportions of the different types of hemoglobin in the blood.",
    why: "Used to look for inherited hemoglobin variants such as thalassemia trait.",
    source: mp("hemoglobin-electrophoresis", "Hemoglobin Electrophoresis"),
  },
  // Iron and vitamins
  {
    match: [/^ferritin$/u],
    title: "Ferritin",
    measures: "The protein that stores iron; the blood level reflects the body's iron stores.",
    why: "The most useful single test for iron deficiency. It also rises with inflammation, so it can look normal during illness.",
    source: mp("ferritin-blood-test", "Ferritin Blood Test"),
  },
  {
    match: [/^iron$/u, /transferrin/u, /^% saturation$/u],
    title: "Iron studies",
    measures: "Iron in the blood, transferrin (the protein that carries it), and the percentage of transferrin carrying iron.",
    why: "Used with ferritin to find iron deficiency or iron overload.",
    source: mp("iron-tests", "Iron Tests"),
  },
  {
    match: [/vitamin b12/u, /folate/u],
    title: "Vitamin B12 and folate",
    measures: "B vitamins needed to make healthy red blood cells and keep nerves working.",
    why: "Checked when red cells are large or for anemia, nerve symptoms, or limited absorption.",
    source: mp("vitamin-b-test", "Vitamin B Test"),
  },
  {
    match: [/vitamin d/u],
    title: "Vitamin D (25-hydroxy)",
    measures: "The main stored form of vitamin D.",
    why: "Vitamin D helps the body absorb calcium. It is often checked to protect bone health, including with long-term steroid treatment.",
    source: mp("vitamin-d-test", "Vitamin D Test"),
  },
  {
    match: [/erythropoietin/u],
    title: "Erythropoietin (EPO)",
    measures: "A hormone made by the kidneys that signals the bone marrow to make red blood cells.",
    why: "Helps explain the cause of anemia or of too many red blood cells.",
  },
  // Clotting
  {
    match: [/^pt$/u, /^inr$/u],
    title: "PT and INR",
    measures: "How long blood takes to clot through one pathway (PT). The INR standardizes it across laboratories.",
    why: "Checks clotting before procedures, liver function, and warfarin treatment.",
    source: mp("prothrombin-time-test-and-inr-ptinr", "PT/INR"),
  },
  {
    match: [/^aptt$/u],
    title: "aPTT (activated partial thromboplastin time)",
    measures: "How long blood takes to clot through a second pathway.",
    why: "Checks clotting before procedures, looks for bleeding disorders, and monitors heparin.",
    source: mp("partial-thromboplastin-time-ptt-test", "PTT Test"),
  },
  {
    match: [/^fibrinogen$/u],
    title: "Fibrinogen",
    measures: "A clotting protein made by the liver.",
    why: "Checked for bleeding problems; it also rises with inflammation.",
  },
  // Inflammation and infection
  {
    match: [/crp/u, /c reactive/u],
    title: "C-reactive protein (CRP)",
    measures: "A protein the liver makes in response to inflammation. The high-sensitivity test measures low levels.",
    why: "A general marker of inflammation or infection; high-sensitivity CRP is also used in heart risk assessment.",
    source: mp("c-reactive-protein-crp-test", "CRP Test"),
  },
  {
    match: [/sedimentation/u],
    title: "ESR (erythrocyte sedimentation rate)",
    measures: "How quickly red blood cells settle in a tube, which speeds up with inflammation.",
    why: "A general, nonspecific marker used to follow inflammatory conditions.",
    source: mp("erythrocyte-sedimentation-rate-esr", "ESR"),
  },
  {
    match: [/covid/u, /sars/u, /flu a b rsv/u],
    title: "Respiratory virus test",
    measures: "Looks for genetic material (PCR or NAT) or proteins (antigen) of viruses such as SARS-CoV-2, influenza, or RSV, usually from a nose swab.",
    why: "Ordered for symptoms, before procedures or admission, or for surveillance in hospitals.",
  },
  {
    match: [/culture/u, /carbapenemase/u],
    title: "Culture",
    measures: "Grows bacteria from a sample to identify them and, if found, test which antibiotics work.",
    why: "Diagnoses infections. Surveillance cultures screen hospital patients for resistant bacteria.",
  },
  // Urine (specific components first, then the general entry below)
  {
    match: [/^culture urine$/u],
    urine: true,
    title: "Urine culture",
    measures: "Grows bacteria from a urine sample to identify them and, if found, test which antibiotics work.",
    why: "Confirms a urinary tract infection and guides treatment.",
  },
  {
    match: [/^(poc )?protein urine$/u, /microalbumin/u],
    urine: true,
    title: "Protein or albumin in urine",
    measures: "Protein, or the protein albumin, that has leaked into the urine.",
    why: "An early sign of kidney stress, for example from diabetes or high blood pressure.",
    source: mp("microalbumin-creatinine-ratio", "Microalbumin Creatinine Ratio"),
  },
  {
    match: [/^(poc )?ketones? urine$/u],
    urine: true,
    title: "Ketones in urine",
    measures: "Ketones are made when the body burns fat for energy.",
    why: "Seen with fasting, low-carbohydrate intake, or poorly controlled diabetes.",
    source: mp("ketones-in-urine", "Ketones in Urine"),
  },
  {
    match: [/^(poc )?glucose urine$/u],
    urine: true,
    title: "Glucose in urine",
    measures: "Sugar that has spilled into the urine.",
    why: "Usually appears when blood sugar is high; some diabetes medicines cause it on purpose.",
    source: mp("glucose-in-urine-test", "Glucose in Urine Test"),
  },
  {
    match: [/^(poc |occult )?blood urine$/u, /^hemoglobin urine$/u, /^(red blood cells|rbc) urine$/u],
    urine: true,
    title: "Blood in urine",
    measures: "Red blood cells or hemoglobin found in the urine.",
    why: "Can come from the kidneys, bladder, or urinary tract, including after procedures such as cystoscopy.",
    source: mp("blood-in-urine", "Blood in Urine"),
  },
  {
    match: [/creatinine.*urine/u],
    urine: true,
    title: "Urine creatinine",
    measures: "Creatinine in a urine sample.",
    why: "Used to check that a 24-hour collection is complete and to correct other urine results for how concentrated the urine is.",
    source: mp("creatinine-test", "Creatinine Test"),
  },
];

/** Urinalysis components without their own entry share this description. */
const URINALYSIS: LabTestGuide = {
  title: "Urinalysis",
  measures: "A urinalysis looks at the urine's appearance, concentration (specific gravity), pH, and chemicals (such as protein, glucose, ketones, nitrite, and leukocyte esterase), and at cells, casts, and bacteria under the microscope.",
  why: "Screens for urinary tract infection, kidney disease, and metabolic conditions such as diabetes. Each item in the report is one part of that picture.",
  source: ency("003579", "Urinalysis"),
};

const BLOOD_GASES: LabTestGuide = {
  title: "Arterial blood gas",
  measures: "Measured on blood from an artery, usually at the bedside: pH, oxygen (pO2), carbon dioxide (pCO2), bicarbonate, base excess, oxygen saturation, and often hemoglobin and electrolytes.",
  why: "Shows how well the lungs move oxygen and carbon dioxide and whether the blood is too acidic or alkaline, typically during surgery, intensive care, or breathing problems.",
  source: ency("003855", "Blood gases"),
};

const ECG: LabTestGuide = {
  title: "ECG measurement",
  measures: "Numbers measured from an electrocardiogram: heart rates, the timing of each part of the heartbeat (PR, QRS, QT and corrected QT intervals), and the electrical axis of the heart (P, QRS, and T axes).",
  why: "Used to check heart rhythm and conduction. QTc is watched with medicines and electrolyte changes, such as low potassium or magnesium, that can lengthen it.",
  source: ency("003868", "Electrocardiogram"),
};

const ECHO: LabTestGuide = {
  title: "Echocardiogram measurement",
  measures: "Heart sizes, wall thicknesses, and blood-flow speeds measured on an ultrasound of the heart. Estimated RVSP and RA pressure are calculated estimates of pressures on the right side of the heart.",
  why: "Used to assess heart structure, pumping, valves, and pressures in the lungs' blood vessels.",
  source: ency("003869", "Echocardiogram"),
};

const STRESS: LabTestGuide = {
  title: "Exercise stress test measurement",
  measures: "The highest heart rate and blood pressure reached during an exercise stress test, and the predicted maximum heart rate for age.",
  why: "Shows how hard the heart worked, which helps judge whether the test was adequate.",
  source: ency("003868", "Electrocardiogram"),
};

const ADMIN: LabTestGuide = {
  title: "Order detail",
  measures: "Not a measurement: a question, comment, or administrative detail recorded with a lab order (for example the draw time, sample tube, or screening questions).",
  why: "Kept because the source system reported it alongside the results.",
};

// ---------------------------------------------------------------------------
// Pulmonary function tests
// ---------------------------------------------------------------------------

const PFT_SOURCE = ency("003853", "Pulmonary function tests");

const PFT_MEASURES: readonly (readonly [RegExp, string])[] = [
  [/^fev1\/fvc/iu, "FEV1/FVC: the fraction of the full breath you can blow out in the first second. A low ratio is the hallmark of airway narrowing (obstruction)."],
  [/^fev1\/fev6/iu, "FEV1/FEV6: a stand-in for FEV1/FVC that uses the first six seconds of the breath."],
  [/^fev1\/svc/iu, "FEV1/SVC: FEV1 compared with a slow vital capacity, another way to look for obstruction."],
  [/^fev3\/fvc/iu, "FEV3/FVC: the fraction of the full breath blown out in three seconds."],
  [/^fev1\b/iu, "FEV1: the volume of air you can blow out in the first second of a forced breath out. It falls with airway narrowing and with smaller lungs."],
  [/^fev[0-9.]+/iu, "FEVx: the volume blown out in the first fraction of a second, three, or six seconds of a forced breath out."],
  [/^fvc/iu, "FVC (forced vital capacity): the total air you can blow out after a full breath in. It falls when the lungs are smaller or stiffer (restriction) or with air trapping."],
  [/^svc/iu, "SVC (slow vital capacity): the full breath out measured slowly instead of forcefully."],
  [/^vc\b/iu, "VC (vital capacity): the largest breath you can move in or out."],
  [/^fef25-75/iu, "FEF25-75: the average flow in the middle half of the forced breath out, which reflects smaller airways."],
  [/^fef50\/fif50/iu, "FEF50/FIF50: compares mid-breath flow out with flow in, which helps spot narrowing in the upper airway."],
  [/^fef50/iu, "FEF50: flow halfway through the forced breath out."],
  [/^fif50/iu, "FIF50: flow halfway through a forced breath in."],
  [/^fet/iu, "FET (forced expiratory time): how long the forced breath out lasted."],
  [/^pefr/iu, "PEFR (peak expiratory flow): the fastest speed of air during a forced breath out."],
  [/^pifr/iu, "PIFR (peak inspiratory flow): the fastest speed of air during a forced breath in."],
  [/^rv\/tlc/iu, "RV/TLC: the share of the total lung capacity that stays in after breathing out fully. It rises with air trapping."],
  [/^tlc/iu, "TLC (total lung capacity): all the air in the lungs after a full breath in. A low TLC confirms restriction."],
  [/^rv\b/iu, "RV (residual volume): the air left in the lungs after breathing out as far as possible."],
  [/^frc/iu, "FRC (functional residual capacity): the air left in the lungs at the end of a normal, relaxed breath out."],
  [/^erv/iu, "ERV (expiratory reserve volume): the extra air you can breathe out after a normal breath out. It is often smaller with higher body weight."],
  [/^ic\b/iu, "IC (inspiratory capacity): the largest breath in after a normal breath out."],
  [/^dlco/iu, "DLCO (diffusing capacity): how well gas passes from the air sacs into the blood. \"Corrected\" adjusts for your hemoglobin, because anemia lowers DLCO."],
  [/^kco/iu, "KCO: diffusing capacity per unit of lung volume (DLCO divided by alveolar volume)."],
  [/^raw/iu, "Raw (airway resistance): how hard it is for air to move through the airways, measured in the body box."],
  [/^sgaw/iu, "sGaw (specific airway conductance): how easily air flows through the airways, adjusted for lung volume."],
  [/^lci/iu, "LCI (lung clearance index): how evenly gas is cleared from the lungs during nitrogen washout; higher means less even."],
  [/^z-score/iu, "Z-score: how far a result is from the predicted value, in standard deviations."],
];

const PFT_VARIANTS: readonly (readonly [RegExp, string])[] = [
  [/actual|acutal/iu, "\"Actual\" is the value measured on the day. \"Pre\" means before any bronchodilator (inhaler) was given."],
  [/% predicted/iu, "\"% Predicted\" compares the measured value with the average for a healthy person of the same age, height, and sex."],
  [/lower predicted/iu, "\"Lower Predicted\" is the lower limit of normal (LLN) for someone like you. Results below it are considered low."],
  [/upper predicted/iu, "\"Upper Predicted\" is the upper limit of normal for someone like you."],
  [/mean predicted/iu, "\"Mean Predicted\" is the average expected value for a healthy person of the same age, height, and sex."],
  [/z-score/iu, "\"Z-Score\" says how many standard deviations the result is from predicted. About -1.64 or lower matches the lower limit of normal."],
];

function pftGuide(name: string): LabTestGuide {
  const measure = PFT_MEASURES.find(([pattern]) => pattern.test(name))?.[1];
  const variant = PFT_VARIANTS.find(([pattern]) => pattern.test(name))?.[1];
  const method = /\(pleth\)/iu.test(name)
    ? " \"(Pleth)\" means it was measured in a body box (plethysmograph)."
    : /\(n2\)/iu.test(name)
      ? " \"(N2)\" means it was measured by nitrogen washout."
      : "";
  const notes = [variant, method.trim()].filter((part): part is string => part !== undefined && part !== "").join(" ");
  return {
    title: "Pulmonary function test",
    measures: measure ?? "One measurement from a pulmonary (lung) function test.",
    why: "Pulmonary function tests measure how much air the lungs hold, how quickly air moves in and out, and how well oxygen passes into the blood. Results are compared with predicted values for age, height, and sex.",
    ...(notes === "" ? {} : { notes }),
    source: PFT_SOURCE,
  };
}

/** The description for one test name, or null when none is written yet. */
export function guideFor(name: string): LabTestGuide | null {
  const category = categorize(name);
  if (category === "Pulmonary function tests (PFTs)") return pftGuide(name);
  const key = testKey(name);
  const urine = category === "Urine";
  const entry = ENTRIES.find((item) => (item.urine === true) === urine && item.match.some((pattern) => pattern.test(key)));
  if (entry !== undefined) {
    return {
      title: entry.title,
      measures: entry.measures,
      why: entry.why,
      ...(entry.notes === undefined ? {} : { notes: entry.notes }),
      ...(entry.source === undefined ? {} : { source: entry.source }),
    };
  }
  switch (category) {
    case "Urine": return URINALYSIS;
    case "Arterial blood gases": return BLOOD_GASES;
    case "Notes and order details": return ADMIN;
    case "Heart: ECG, echo, and stress test":
      if (/^max /iu.test(name)) return STRESS;
      return /(axis|interval|qtc|qrs|rate|q-t|p-r)/iu.test(name) ? ECG : ECHO;
    default: return null;
  }
}
