import { cp, mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(root, "src");
const dist = join(root, "dist");
const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const releaseSha = process.env.RELEASE_SHA ?? "unknown";

await mkdir(dist, { recursive: true });
await cp(src, dist, { recursive: true });
await writeFile(
  join(dist, "config.js"),
  `window.CONVICTIONOS_CONFIG = ${JSON.stringify({ apiBaseUrl, releaseSha })};\n`,
  "utf8",
);

console.log(`Built ConvictionOS web to ${dist}`);
