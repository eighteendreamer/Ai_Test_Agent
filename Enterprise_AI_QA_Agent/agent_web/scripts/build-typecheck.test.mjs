import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

describe("production build type checks", () => {
  for (const command of ["build", "desktop:build"]) {
    it(`blocks ${command} before replacing artifacts when TS or Vue templates have errors`, () => {
      const tempRoot = resolve(tmpdir());
      const fixture = mkdtempSync(join(tempRoot, "qa-agent-build-types-"));
      try {
        mkdirSync(join(fixture, "scripts"));
        mkdirSync(join(fixture, "src"));
        mkdirSync(join(fixture, "dist"));
        symlinkSync(join(appRoot, "node_modules"), join(fixture, "node_modules"), "junction");
        copyFileSync(join(appRoot, "package.json"), join(fixture, "package.json"));
        copyFileSync(join(appRoot, "tsconfig.json"), join(fixture, "tsconfig.json"));
        copyFileSync(join(appRoot, "scripts", "build-desktop-renderer.mjs"), join(fixture, "scripts", "build-desktop-renderer.mjs"));
        writeFileSync(join(fixture, "dist", "index.html"), "previous successful artifact");
        writeFileSync(join(fixture, "src", "invalid.ts"), 'export const count: number = "wrong type";\n');
        writeFileSync(join(fixture, "src", "Invalid.vue"), '<script setup lang="ts">const count = 1;</script>\n<template>{{ count.toUpperCase() }}</template>\n');
        // A valid Vite entry makes the old transpilation-only build produce output.
        writeFileSync(join(fixture, "index.html"), '<div id="app"></div><script type="module" src="/src/invalid.ts"></script>');

        const result = spawnSync("npm", ["run", command], {
          cwd: fixture,
          shell: true,
          encoding: "utf8",
          timeout: 60000,
        });
        const output = `${result.stdout || ""}\n${result.stderr || ""}`;
        expect(result.error, output).toBeUndefined();
        expect(result.status, output).not.toBe(0);
        expect(output).toMatch(/invalid\.ts.*TS2322/);
        expect(output).toMatch(/Invalid\.vue.*TS2339/);
        expect(output).not.toMatch(/building for production/);
        expect(readFileSync(join(fixture, "dist", "index.html"), "utf8")).toBe("previous successful artifact");
      } finally {
        if (dirname(fixture) !== tempRoot || !basename(fixture).startsWith("qa-agent-build-types-")) {
          throw new Error(`Unexpected build fixture cleanup path: ${fixture}`);
        }
        // Remove the junction first so cleanup can never descend into app dependencies.
        rmSync(join(fixture, "node_modules"), { force: true, recursive: true });
        rmSync(fixture, { force: true, recursive: true });
      }
    }, 90000);
  }
});
