import { readFile, readdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const files = [
  "src/index.html",
  "src/dashboard.html",
  "src/styles.css",
  "src/dashboard.js",
  "vercel.json",
];
const forbidden = [/api[_-]?key/i, /secret/i, /guaranteed/i, /profitable/i, /live trading/i];

for (const file of files) {
  const text = await readFile(join(root, file), "utf8");
  for (const pattern of forbidden) {
    if (pattern.test(text) && !file.endsWith("check-web.mjs")) {
      throw new Error(`${file} contains forbidden public wording: ${pattern}`);
    }
  }
}

const sourceFiles = await readdir(join(root, "src"));
for (const required of ["index.html", "dashboard.html", "styles.css", "dashboard.js"]) {
  if (!sourceFiles.includes(required)) {
    throw new Error(`missing ${required}`);
  }
}

console.log("web checks ok");
