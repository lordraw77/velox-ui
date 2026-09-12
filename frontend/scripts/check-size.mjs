/**
 * Enforce the bundle budget.
 *
 * The budget is on the *initial* bundle: what a browser must download before it can
 * show a conversation. Lazily loaded chunks — the markdown worker, each syntax
 * grammar — are reported but not counted, because a conversation with no code never
 * downloads a highlighter. Counting them would make the number meaningless in the
 * other direction: it would punish exactly the code-splitting that keeps startup fast.
 *
 * gzip rather than brotli: it is what every server and proxy has, so it is the size a
 * user is most likely to actually pay.
 */

import { gzipSync } from "node:zlib";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const DIST = new URL("../../src/velox_ui/web/assets/", import.meta.url).pathname;
const BUDGET_BYTES = 200 * 1024;

/** Chunks a browser fetches before first paint: the entry script and its stylesheet. */
const EAGER = /^index-.*\.(js|css)$/;

function gzipSize(path) {
  return gzipSync(readFileSync(path), { level: 9 }).length;
}

function kib(bytes) {
  return `${(bytes / 1024).toFixed(1)} KB`;
}

const files = readdirSync(DIST).filter((name) => /\.(js|css)$/.test(name));
if (files.length === 0) {
  console.error("check-size: no build output found. Run `npm run build` first.");
  process.exit(2);
}

const eager = [];
const lazy = [];
for (const name of files) {
  const entry = { name, gzip: gzipSize(join(DIST, name)) };
  (EAGER.test(name) ? eager : lazy).push(entry);
}

eager.sort((a, b) => b.gzip - a.gzip);
lazy.sort((a, b) => b.gzip - a.gzip);

const initial = eager.reduce((total, entry) => total + entry.gzip, 0);
const deferred = lazy.reduce((total, entry) => total + entry.gzip, 0);

console.log("initial bundle (gzip):");
for (const entry of eager) console.log(`  ${entry.name.padEnd(34)} ${kib(entry.gzip)}`);
console.log(`  ${"total".padEnd(34)} ${kib(initial)}  budget ${kib(BUDGET_BYTES)}`);
console.log(`\nlazily loaded, not counted: ${lazy.length} chunks, ${kib(deferred)} gzip`);
for (const entry of lazy.slice(0, 5)) {
  console.log(`  ${entry.name.padEnd(34)} ${kib(entry.gzip)}`);
}

if (initial > BUDGET_BYTES) {
  console.error(`\ncheck-size: FAIL — initial bundle is ${kib(initial)}, budget is ${kib(BUDGET_BYTES)}`);
  process.exit(1);
}
console.log(`\ncheck-size: pass — ${((initial / BUDGET_BYTES) * 100).toFixed(0)}% of budget used`);
