// Launch Vite with HMR, then open Electron against that live origin.
// `npm run desktop` still serves a static build and will not hot-reload.
import { spawn } from "node:child_process";
import { request as httpRequest } from "node:http";

const isWindows = process.platform === "win32";
const viteOrigin = process.env.QA_AGENT_RENDERER_ORIGIN || "http://127.0.0.1:5175";
const children = [];
let shuttingDown = false;

function spawnNpm(script, extraEnv = {}) {
  const child = spawn("npm", ["run", script], {
    cwd: process.cwd(),
    shell: true,
    env: { ...process.env, ...extraEnv },
    stdio: "inherit",
  });
  children.push(child);
  return child;
}

function waitForHttp(url, timeoutMs = 60000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = httpRequest(url, { method: "GET" }, (response) => {
        response.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() - started > timeoutMs) {
          reject(new Error(`Vite did not become ready at ${url}`));
          return;
        }
        setTimeout(tryOnce, 300);
      });
      req.end();
    };
    tryOnce();
  });
}

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (child.exitCode !== null) continue;
    if (isWindows) {
      spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
        shell: true,
        stdio: "ignore",
      });
    } else {
      child.kill("SIGTERM");
    }
  }
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

const vite = spawnNpm("dev:app");
vite.on("exit", (code) => {
  if (!shuttingDown) {
    console.error(`[desktop:dev] Vite exited with code ${code ?? 0}`);
    shutdown();
    process.exit(code ?? 1);
  }
});

try {
  await waitForHttp(viteOrigin);
} catch (error) {
  console.error(`[desktop:dev] ${error instanceof Error ? error.message : error}`);
  shutdown();
  process.exit(1);
}

const electron = spawn("npx", ["electron", "electron/main.js"], {
  cwd: process.cwd(),
  shell: true,
  env: {
    ...process.env,
    QA_AGENT_RENDERER_ORIGIN: viteOrigin,
  },
  stdio: "inherit",
});
children.push(electron);
electron.on("exit", (code) => {
  shutdown();
  process.exit(code ?? 0);
});
