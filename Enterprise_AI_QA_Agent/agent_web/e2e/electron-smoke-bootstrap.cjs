// Test-only process bootstrap. Keep desktop data and shortcuts in a fresh profile.
// Native windows stay off screen with normal visibility; production code is unchanged.
const assert = require("node:assert/strict");
const { app, BrowserWindow, webContents, shell } = require("electron");
const { mkdirSync } = require("node:fs");
const { join } = require("node:path");
const { pathToFileURL } = require("node:url");

const profile = process.env.QA_AGENT_SMOKE_PROFILE;
const main = process.env.QA_AGENT_SMOKE_MAIN;
if (!profile || !main) throw new Error("Desktop smoke profile and main are required");
for (const name of ["appData", "userData", "logs"]) {
  const path = join(profile, name);
  mkdirSync(path, { recursive: true });
  app.setPath(name, path);
}
// Simulate release debug policy while launching through the test bootstrap.
Object.defineProperty(app, "isPackaged", { value: true });
let resolveMain;
const mainCreated = new Promise((done) => { resolveMain = done; });
app.on("browser-window-created", (_event, win) => {
  win.setPosition(-32000, -32000);
  if (resolveMain) { resolveMain(win); resolveMain = null; }
});
const result = { checks: [], pageErrors: [], debugCommands: [] };
app.on("web-contents-created", (_event, contents) => {
  contents.on("console-message", (details) => {
    if (details.level === "error") result.pageErrors.push(details.message);
  });
  contents.on("preload-error", (_event, _path, error) => result.pageErrors.push(error.message));
  const send = contents.debugger.sendCommand.bind(contents.debugger);
  contents.debugger.sendCommand = (...args) => {
    const entry = { method: args[0], contentsId: contents.id, state: "pending" };
    result.debugCommands.push(entry);
    return send(...args).then((value) => { entry.state = "resolved"; return value; }, (error) => {
      entry.state = "rejected"; entry.error = error.message; throw error;
    });
  };
});
let finished = false;
function finish(error) {
  if (finished) return;
  finished = true;
  result.status = error ? "failed" : "passed";
  if (error) result.error = error.stack;
  console.log("QA_AGENT_DESKTOP_SMOKE=" + JSON.stringify(result));
  app.exit(error ? 1 : 0);
}
const deadline = setTimeout(() => finish(new Error("Native desktop smoke timed out after 45 seconds")), 45000);
async function until(check, description) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await check()) return;
    await new Promise((done) => setTimeout(done, 50));
  }
  throw new Error("Desktop smoke wait timed out: " + description);
}
async function run() {
  await import(pathToFileURL(main).href);
  const win = await mainCreated;
  const runInRenderer = (expression) => win.webContents.executeJavaScript(expression);
  await until(() => runInRenderer("!!document.querySelector('.assistant-markdown .katex')"), "Markdown mounted");
  assert.equal(await runInRenderer("document.querySelector('.assistant-markdown pre.hljs code').innerText"), "print('loaded')\n");
  assert.deepEqual(await runInRenderer("({desktop:window.qaAgentDesktop?.isDesktop,node:typeof window.require,process:typeof window.process})"), { desktop: true, node: "undefined", process: "undefined" });
  const prefs = win.webContents.getLastWebPreferences();
  result.preferences = { sandbox: prefs.sandbox, contextIsolation: prefs.contextIsolation, nodeIntegration: prefs.nodeIntegration };
  assert.deepEqual(result.preferences, { sandbox: true, contextIsolation: true, nodeIntegration: false });
  result.versions = process.versions;
  result.checks.push("main/static renderer/Markdown/preload/isolation");
  assert.equal(await runInRenderer("window.qaAgentDesktop.setZoomFactor(1.1)"), 1.1);
  assert.equal(win.webContents.getZoomFactor(), 1.1);
  result.checks.push("preload zoom");
  const opened = [];
  shell.openExternal = async (url) => { opened.push(url); };
  await runInRenderer("window.open('mailto:probe@example.invalid','_blank');window.open('https://example.invalid/desktop-probe','_blank');void 0");
  await until(() => opened.length > 0, "window-open handled");
  assert.deepEqual(opened, ["https://example.invalid/desktop-probe"]);
  result.checks.push("window-open protocol policy (OS open stubbed)");
  assert.equal(await runInRenderer("window.qaAgentDesktop.openFlowWindow({sessionId:'desktop-runtime-probe'})"), true);
  await until(() => BrowserWindow.getAllWindows().some((item) => item !== win && item.webContents.getURL().includes("/flow?")), "flow loaded");
  assert.equal(await runInRenderer("window.qaAgentDesktop.openFlowWindow({sessionId:'desktop-runtime-probe'})"), true);
  assert.equal(BrowserWindow.getAllWindows().length, 2);
  BrowserWindow.getAllWindows().find((item) => item !== win).close();
  result.checks.push("flow window creation/reuse");
  const recordingId = "desktop-recording-probe";
  const target = process.env.QA_AGENT_API_ORIGIN + "/fixture/target";
  const payload = JSON.stringify({ recordingId, entryUrl: target });
  assert.deepEqual(await runInRenderer(`window.qaAgentDesktop.recorder.createWindow(${payload})`), { recordingId, reused: false });
  const state = await runInRenderer(`window.qaAgentDesktop.recorder.getState('${recordingId}')`);
  assert.equal(state.attached, true);
  assert.equal(await runInRenderer(`window.qaAgentDesktop.recorder.setCapture('${recordingId}',true)`), true);
  const view = webContents.getAllWebContents().find((item) => item.getURL() === target);
  assert(view, "Recorder view must load the fixture");
  await view.executeJavaScript("document.querySelector('#probe').click()");
  assert.equal(await runInRenderer(`window.qaAgentDesktop.recorder.captureScreenshot('${recordingId}')`), "fixture:screenshot");
  assert.equal(await runInRenderer(`window.qaAgentDesktop.recorder.close('${recordingId}')`), true);
  assert.equal(await runInRenderer(`window.qaAgentDesktop.recorder.getState('${recordingId}')`), null);
  result.checks.push("recorder injection/registration/capture/event upload/close");
  clearTimeout(deadline);
}
run().then(() => finish(), finish);
