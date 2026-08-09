import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const temporaryDirectory = mkdtempSync(join(tmpdir(), "healthcurve-openapi-"));
const generatedPath = join(temporaryDirectory, "schema.d.ts");

try {
  const result = spawnSync(
    "openapi-typescript",
    ["openapi.json", "-o", generatedPath],
    { encoding: "utf8", shell: false, stdio: "inherit" },
  );
  if (result.status !== 0) {
    process.exitCode = result.status ?? 1;
  } else if (readFileSync(generatedPath, "utf8") !== readFileSync("src/api/schema.d.ts", "utf8")) {
    console.error(
      "Generated API types are stale. Run `make frontend-generate` and commit the result.",
    );
    process.exitCode = 1;
  }
} finally {
  rmSync(temporaryDirectory, { force: true, recursive: true });
}
