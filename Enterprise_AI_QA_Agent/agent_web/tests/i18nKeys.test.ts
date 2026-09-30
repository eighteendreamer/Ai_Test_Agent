import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import enUS from "../src/locales/en-US";
import zhCN from "../src/locales/zh-CN";

const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url));
const enKeys = new Set(Object.keys(enUS));
const zhKeys = new Set(Object.keys(zhCN));

function sourceTranslationKeys(directory: string): Set<string> {
  const keys = new Set<string>();
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === "locales") continue;
    const location = join(directory, entry.name);
    if (entry.isDirectory()) {
      for (const key of sourceTranslationKeys(location)) keys.add(key);
      continue;
    }
    if (!/\.(ts|vue)$/.test(entry.name) || entry.name.endsWith(".test.ts")) continue;
    const source = readFileSync(location, "utf8");
    for (const match of source.matchAll(/\bt\(\s*["']([^"']+)["']/g)) keys.add(match[1]);
  }
  return keys;
}

it("defines every static UI translation key in a base locale", () => {
  const missing = [...sourceTranslationKeys(sourceRoot)]
    .filter((key) => !zhKeys.has(key) && !enKeys.has(key)).sort();
  expect(missing).toEqual([]);
});

it("keeps the Chinese and English base dictionaries aligned", () => {
  expect([...zhKeys].filter((key) => !enKeys.has(key)).sort()).toEqual([]);
  expect([...enKeys].filter((key) => !zhKeys.has(key)).sort()).toEqual([]);
});
