import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const appRoot = process.cwd();
const appDist = resolve(appRoot, "dist");
const docsDist = resolve(appRoot, "docs", ".vitepress", "dist");
const bundledDocsDist = resolve(appDist, "docs");

function run(command, args, env = {}) {
  const result = spawnSync(command, args, {
    cwd: appRoot,
    shell: true,
    stdio: "inherit",
    env: { ...process.env, ...env },
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

run("npm", ["run", "build"], {
  VITE_QA_AGENT_DESKTOP: "1",
});
run("vitepress", ["build", "docs"], {
  DOCS_BASE: "/docs/",
  DOCS_APP_URL: "/home",
});

if (!existsSync(resolve(appDist, "index.html"))) {
  throw new Error("App build output not found.");
}

if (!existsSync(resolve(docsDist, "index.html"))) {
  throw new Error("Docs build output not found.");
}

rmSync(bundledDocsDist, { recursive: true, force: true });
mkdirSync(bundledDocsDist, { recursive: true });
cpSync(docsDist, bundledDocsDist, { recursive: true });

console.log(`Bundled docs at ${bundledDocsDist}`);
