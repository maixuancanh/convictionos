import assert from "node:assert/strict";
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

const landing = await readFile(join(root, "src/index.html"), "utf8");
const dashboard = await readFile(join(root, "src/dashboard.html"), "utf8");
const dashboardScript = await readFile(join(root, "src/dashboard.js"), "utf8");
const vercel = JSON.parse(await readFile(join(root, "vercel.json"), "utf8"));

for (const required of [
  "Evidence earns capital",
  "From evidence to an auditable decision",
  "Built for individual investors and quant teams",
  "Strategy lifecycle",
  "Paper capital",
  "Production discipline",
  "Performance without performance theater",
]) {
  assert.match(landing, new RegExp(required.replaceAll(".", "\\.")));
}

for (const required of [
  "Overview",
  "Agents",
  "Portfolio",
  "Strategies",
  "Decisions",
  "Risk controls",
  "Research Lab",
  "Recent decisions",
  "Strategy mandates",
  "System health",
]) {
  assert.match(dashboard, new RegExp(required));
}

assert.match(dashboard, /aria-label="Open navigation"/);
assert.match(dashboard, /aria-live="polite"/);
assert.match(dashboardScript, /competition-readiness/);
assert.match(dashboardScript, /runtime\/status/);
assert.match(landing, /href="\/mission-control(?:#risk)?"/);
assert.doesNotMatch(landing, /href="\/dashboard(?:#risk)?"/);
assert.deepEqual(vercel.rewrites, [
  { source: "/dashboard", destination: "/index.html" },
  { source: "/mission-control", destination: "/dashboard.html" },
]);

const sourceFiles = await readdir(join(root, "src"));
for (const required of ["index.html", "dashboard.html", "styles.css", "dashboard.js"]) {
  if (!sourceFiles.includes(required)) {
    throw new Error(`missing ${required}`);
  }
}

console.log("web checks ok");
