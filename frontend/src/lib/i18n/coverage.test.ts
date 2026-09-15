/**
 * Catalogue coverage.
 *
 * Two failure modes, both silent at runtime and both found here instead:
 *
 * - A `t("chat.snd")` typo falls back to returning the key, so the interface ships a
 *   literal `chat.snd` to a user. Nothing throws.
 * - A key nobody uses accumulates in every catalogue, and a translator spends effort
 *   on a string that will never appear. This test found `auth.createAdmin`, which was
 *   written and then never referenced.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import en from "./en.json";

const SOURCE_ROOT = new URL("../..", import.meta.url).pathname;

function walk(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) found.push(...walk(path));
    else if (/\.(ts|svelte)$/.test(entry) && !entry.endsWith(".test.ts")) found.push(path);
  }
  return found;
}

/** Keys referenced through `t("…")` or `app.t("…")`, plus those in a lookup table. */
function referencedKeys(): Set<string> {
  const keys = new Set<string>();
  const call = /\bt\(\s*"([a-z][\w.]*)"/gi;
  // Keys held in a map and looked up dynamically, as the status and error components do.
  const literal =
    /"((?:status|error|providers|metrics|models|chat|auth|theme|common|app|nav|params|local|jobs|admin|search|knowledge|plugins)\.[\w]+)"/g;

  for (const file of walk(SOURCE_ROOT)) {
    const source = readFileSync(file, "utf-8");
    for (const match of source.matchAll(call)) if (match[1]) keys.add(match[1]);
    for (const match of source.matchAll(literal)) if (match[1]) keys.add(match[1]);
  }
  return keys;
}

describe("i18n coverage", () => {
  it("has a catalogue entry for every key the source references", () => {
    const catalogue = new Set(Object.keys(en));
    const missing = [...referencedKeys()].filter((key) => !catalogue.has(key)).sort();
    expect(missing, "these keys are used but not defined; the UI would show the key itself").toEqual(
      [],
    );
  });

  it("references every key the catalogue defines", () => {
    const used = referencedKeys();
    const unused = Object.keys(en).filter((key) => !used.has(key)).sort();
    expect(unused, "these catalogue entries are never used; delete them or use them").toEqual([]);
  });
});
