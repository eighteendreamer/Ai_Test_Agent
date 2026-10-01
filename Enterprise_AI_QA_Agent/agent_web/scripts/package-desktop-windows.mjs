import { createRequire } from "node:module";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const require = createRequire(import.meta.url);
// main.js imports this dependency in the Electron main process. The renderer
// bundle cannot supply it when the portable app leaves the development tree.
const markdownPackagePath = require.resolve("remove-markdown/package.json");
const markdownPackage = JSON.parse(readFileSync(markdownPackagePath, "utf8"));
const rceditModule = require("rcedit");
const rcedit = rceditModule.rcedit || rceditModule;

const appRoot = process.cwd();
const electronDist = resolve(appRoot, "node_modules", "electron", "dist");
const rendererRoot = resolve(appRoot, "dist");
const sourceRoot = resolve(appRoot, "electron");
const assetsRoot = resolve(appRoot, "desktop-assets");
const iconIco = resolve(assetsRoot, "logo.ico");
const releaseRoot = resolve(appRoot, "release-build", "win-unpacked");
const packagedAppRoot = resolve(releaseRoot, "resources", "app");
const productExe = resolve(releaseRoot, "御策天检.exe");
const electronExe = resolve(releaseRoot, "electron.exe");

if (!existsSync(resolve(rendererRoot, "index.html"))) {
  throw new Error("Renderer build not found. Run `npm run build` first.");
}

if (!existsSync(resolve(electronDist, "electron.exe"))) {
  throw new Error(`Electron runtime not found: ${electronDist}`);
}

rmSync(releaseRoot, { recursive: true, force: true });
mkdirSync(releaseRoot, { recursive: true });
cpSync(electronDist, releaseRoot, { recursive: true });

if (existsSync(electronExe)) {
  rmSync(productExe, { force: true });
  cpSync(electronExe, productExe);
  rmSync(electronExe, { force: true });
}

rmSync(packagedAppRoot, { recursive: true, force: true });
mkdirSync(packagedAppRoot, { recursive: true });
cpSync(sourceRoot, resolve(packagedAppRoot, "electron"), { recursive: true });
cpSync(rendererRoot, resolve(packagedAppRoot, "dist"), { recursive: true });
cpSync(assetsRoot, resolve(packagedAppRoot, "desktop-assets"), { recursive: true });
cpSync(dirname(markdownPackagePath), resolve(packagedAppRoot, "node_modules", "remove-markdown"), { recursive: true });

writeFileSync(
  resolve(packagedAppRoot, "package.json"),
  JSON.stringify(
    {
      name: "enterprise-ai-qa-agent-desktop-runtime",
      version: "0.1.0",
      type: "module",
      main: "electron/main.js",
      dependencies: { "remove-markdown": markdownPackage.version },
    },
    null,
    2,
  ),
  "utf8",
);

if (existsSync(iconIco)) {
  await rcedit(productExe, {
    icon: iconIco,
  });
}

console.log(`Desktop app packaged at ${releaseRoot}`);
console.log(`Launch: ${productExe}`);
