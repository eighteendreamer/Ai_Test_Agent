// Real Electron compatibility probe with successful local backend fixtures.
// No external model, production account, OS notification or external browser.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { createRequire } from "node:module";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const { values } = parseArgs({ options: {
  "package-app": { type: "string" },
  output: { type: "string" },
} });
assert(values.output, "Pass --output");
const require = createRequire(import.meta.url);
const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(values.output);
assert(!existsSync(output), "Use a fresh report path to preserve previous results");
mkdirSync(dirname(output), { recursive: true });
const temporaryRoot = resolve(tmpdir());
const profile = mkdtempSync(join(temporaryRoot, "qa-agent-electron-smoke-"));
let productionRoot = appRoot;
if (values["package-app"]) {
  productionRoot = join(profile, "portable-app");
  cpSync(resolve(values["package-app"]), productionRoot, { recursive: true });
}
const recorderScript = readFileSync(join(appRoot, "..", "Agent_Server", "src", "application", "recorder", "assets", "recorder.js"), "utf8");
const authToken = randomBytes(24).toString("hex");
const sessionId = "desktop-runtime-probe";
const recordingId = "desktop-recording-probe";
const now = new Date().toISOString();
const session = {
  id: sessionId, title: "Desktop runtime probe", status: "idle",
  session_mode: "normal", runtime_mode: "interactive", mode_key: "default",
  created_at: now, updated_at: now, messages: [{
    id: "answer", role: "assistant", created_at: now,
    content: "# Desktop regression\n\n$$x^2 + y^2 = z^2$$\n\n```python\nprint('loaded')\n```",
    metadata: { delivery_status: "completed" },
  }],
  event_count: 0, snapshot_count: 0, pending_approvals: [], control_state: "idle",
  is_resumable: false, is_interrupted: false, replay_available: false,
  verification_results: [], metadata: {},
};
const report = {
  generatedAt: now, status: "running", portableApp: Boolean(values["package-app"]),
  scope: "Real Electron/production main, preload and recorder; local backend fixtures; off-screen windows with normal visibility and isolated profile. Release debug policy simulated; no model, OS notification or external browser.",
  checks: [], pageErrors: [], unexpectedAPI: [], authFailures: [], requests: [],
};
const git = spawnSync("git", ["rev-parse", "HEAD"], { cwd: appRoot, encoding: "utf8", windowsHide: true });
report.producerHead = git.status === 0 ? git.stdout.trim() : null;
report.controllerNode = process.versions.node;
report.inputHashes = Object.fromEntries([
  ["lock", join(appRoot, "package-lock.json")],
  ["controller", fileURLToPath(import.meta.url)],
  ["bootstrap", join(appRoot, "e2e", "electron-smoke-bootstrap.cjs")],
  ...["main.js", "preload.cjs", "recorder-window.mjs", "api-auth-proxy.mjs", "external-url-policy.mjs"].map((name) => [name, join(productionRoot, "electron", name)]),
  ["recorder.js", join(appRoot, "..", "Agent_Server", "src", "application", "recorder", "assets", "recorder.js")],
].map(([name, path]) => [name, createHash("sha256").update(readFileSync(path)).digest("hex")]));
const events = [];
let screenshotBytes = 0;
const server = createServer(async (request, response) => {
  try {
    const path = new URL(request.url, "http://localhost").pathname;
    if (path === "/fixture/target") {
      response.setHeader("content-type", "text/html");
      response.end('<!doctype html><title>Recorder fixture</title><button id="probe">Record click</button>');
      return;
    }
    report.requests.push(`${request.method} ${path}`);
    if (request.headers.authorization !== `Bearer ${authToken}`) report.authFailures.push(path);
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    let payload;
    if (path === "/api/v1/recordings/recorder.js") {
      response.setHeader("content-type", "application/javascript");
      response.end(recorderScript);
      return;
    } else if (path.endsWith("/events:batch")) {
      events.push(...JSON.parse(body.toString()).events);
      payload = { ok: true };
    } else if (path.endsWith("/screenshots")) {
      screenshotBytes += body.length;
      payload = { ref: "fixture:screenshot" };
    } else if (path.endsWith("/attach-registry") || path.endsWith("/control")) {
      payload = { ok: true };
    } else if (path.endsWith("/commands")) {
      await new Promise((done) => setTimeout(done, 100));
      payload = { commands: [] };
    } else if (path === `/api/v1/recordings/${recordingId}`) {
      payload = { id: recordingId, status: "active", step_count: events.length,
        entry_url: `http://127.0.0.1:${server.address().port}/fixture/target` };
    } else if (path === `/api/v1/sessions/${sessionId}/flow`) {
      payload = { session_id: sessionId, turn_id: "", stages: [], edges: [], snapshot_id: null,
        graph_state: null, workers: [], events: [], tool_jobs: [], artifacts: [], langsmith_trace: {} };
    } else if (path === "/api/v1/settings/general") {
      payload = {};
    } else if (path === "/api/v1/health") {
      payload = { status: "ok" };
    } else if (["/api/v1/registry/agents", "/api/v1/registry/tools/summary", "/api/v1/registry/modes", "/api/v1/settings/models", "/api/v1/sponsors", "/api/v1/recordings"].includes(path)
      || path.endsWith("/tool-jobs") || path.endsWith("/artifacts") || path.endsWith("/events/history") || path.endsWith("/snapshots")) {
      payload = [];
    } else if (path === "/api/v1/sessions" || path === `/api/v1/sessions/${sessionId}`) {
      payload = session;
    } else if (path.endsWith("/verifications")) {
      payload = { session_id: sessionId, verification_results: [], metadata: {} };
    } else if (path.endsWith("/events")) {
      response.setHeader("content-type", "text/event-stream");
      response.end(": ready\n\n");
      return;
    } else {
      report.unexpectedAPI.push(`${request.method} ${path}`);
      response.statusCode = 501;
      payload = { detail: "Missing smoke fixture" };
    }
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify(payload));
  } catch (error) {
    report.unexpectedAPI.push(`fixture error: ${error.message}`);
    response.statusCode = 500;
    response.end("fixture error");
  }
});
let child;
try {
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  const backendOrigin = `http://127.0.0.1:${server.address().port}`;
  const env = { ...process.env, QA_AGENT_API_ORIGIN: backendOrigin,
    API_AUTH_TOKEN: authToken, QA_AGENT_SMOKE_PROFILE: profile,
    QA_AGENT_SMOKE_MAIN: join(productionRoot, "electron", "main.js"),
  };
  delete env.QA_AGENT_RENDERER_ORIGIN;
  delete env.QA_AGENT_DESKTOP_DEBUG;
  delete env.ELECTRON_RUN_AS_NODE;
  child = spawn(require(join(appRoot, "node_modules", "electron")),
    [join(appRoot, "e2e", "electron-smoke-bootstrap.cjs")],
    { cwd: productionRoot, env, windowsHide: true });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const code = await new Promise((done, reject) => {
    child.once("error", reject);
    child.once("close", done);
  });
  report.stdout = stdout;
  report.stderr = stderr;
  const resultLine = stdout.split(/\r?\n/).find((line) => line.startsWith("QA_AGENT_DESKTOP_SMOKE="));
  assert(resultLine, "Native smoke process must provide its result");
  const result = JSON.parse(resultLine.slice("QA_AGENT_DESKTOP_SMOKE=".length));
  report.checks = result.checks;
  report.pageErrors = result.pageErrors;
  report.versions = result.versions;
  report.preferences = result.preferences;
  report.debugCommands = result.debugCommands;
  assert.equal(code, 0, result.error || stderr);
  assert.equal(result.status, "passed", result.error);
  assert(screenshotBytes > 0);
  assert(events.some((event) => event.type === "click"), "Recorder must flush its captured DOM click on close");
  assert.deepEqual(report.authFailures, []);
  assert.deepEqual(report.unexpectedAPI, []);
  assert.deepEqual(report.pageErrors, []);
  report.checks.push("backend authentication/known requests/no renderer errors");
  report.status = "passed";
} catch (error) {
  report.status = "failed";
  report.error = error.stack;
  process.exitCode = 1;
} finally {
  server.closeAllConnections();
  await new Promise((done) => server.close(done));
  if (dirname(profile) !== temporaryRoot || !basename(profile).startsWith("qa-agent-electron-smoke-")) {
    throw new Error("Unexpected smoke profile cleanup path");
  }
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  } catch (error) {
    report.status = "failed";
    report.cleanupError = error.message;
    process.exitCode = 1;
  }
  writeFileSync(output, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ status: report.status, checks: report.checks, electron: report.versions?.electron, error: report.error }));
}
