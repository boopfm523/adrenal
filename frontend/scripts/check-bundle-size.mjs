import { readdir, stat } from "node:fs/promises";
import { join } from "node:path";

const limitKiB = 450;
const assetsDirectory = new URL("../dist/assets/", import.meta.url);
const files = (await readdir(assetsDirectory)).filter((name) => name.endsWith(".js"));
const results = await Promise.all(files.map(async (name) => ({
  name,
  bytes: (await stat(join(assetsDirectory.pathname, name))).size,
})));
const oversized = results.filter(({ bytes }) => bytes > limitKiB * 1024);

if (oversized.length > 0) {
  const detail = oversized.map(({ name, bytes }) => `${name}: ${(bytes / 1024).toFixed(1)} KiB`).join(", ");
  throw new Error(`JavaScript chunk budget exceeded (${limitKiB} KiB): ${detail}`);
}

const largest = results.toSorted((left, right) => right.bytes - left.bytes)[0];
console.log(`Bundle budget passed: largest JavaScript chunk ${largest === undefined ? "none" : `${largest.name} ${(largest.bytes / 1024).toFixed(1)} KiB`} <= ${limitKiB} KiB.`);
