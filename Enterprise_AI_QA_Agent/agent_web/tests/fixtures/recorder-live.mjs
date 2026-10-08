// Functional harness: the original Electron recorder talks to the original HTTP API.
import { app, BrowserWindow, webContents } from "electron";
import { readFileSync, writeFileSync, appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const configPath = process.argv.find(value => value.endsWith("electron.json"));
const config = JSON.parse(readFileSync(configPath, "utf8"));
mkdirSync(config.userData, { recursive: true });
app.setPath("userData", config.userData);
app.on("window-all-closed", () => {});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const save = (name, value) => writeFileSync(join(config.evidence, name), JSON.stringify(value, null, 2));
const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, options) => {
  const response = await originalFetch(url, options);
  if (String(url).includes("/api/v1/recordings/")) {
    const body = await response.clone().text();
    appendFileSync(join(config.evidence, "electron-http.jsonl"), JSON.stringify({
      url: String(url), method: options?.method || "GET", request: options?.body instanceof FormData ? "multipart screenshot" : options?.body || null,
      status: response.status, response: body,
    }) + "\n");
  }
  return response;
};

save("harness-start.json", { pid: process.pid, version: process.versions.electron, ready: app.isReady() });
async function run() {
let recorder;
try {
  await app.whenReady();
  recorder = await import(pathToFileURL(config.module));
  recorder.configureRecorderBridge({ backendOrigin: config.origin, rendererOrigin: config.origin });
  await recorder.createRecorderWindow({ recordingId: config.recordingId, entryUrl: config.entryUrl, name: "受控录制验收" });
  let last = 0;
  save("harness-ready.json", { ready: true, version: process.versions.electron });
  for (;;) {
    let command;
    try { command = JSON.parse(readFileSync(config.commands, "utf8")); } catch { await pause(50); continue; }
    if (command.id <= last) { await pause(50); continue; }
    last = command.id;
    const contents = webContents.getAllWebContents().find(item => item.getURL() === config.entryUrl);
    if (contents && !contents.__testObserved) {
      contents.__testObserved = true;
      contents.debugger.on("message", (_event, method, params) => {
        if (method === "Runtime.bindingCalled" && params.name === "__qaRecordEmit") {
          appendFileSync(join(config.evidence, "binding.jsonl"), params.payload + "\n");
        }
      });
    }
    async function evaluate(expression) {
      const result = await contents.debugger.sendCommand("Runtime.evaluate", { expression, returnByValue: true });
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    }
    if (command.action === "quit") break;
    if (command.action === "snapshot") {
      // Capture a normally rendered original recording view.
      for (const window of BrowserWindow.getAllWindows()) { window.show(); window.focus(); }
      contents.focus();
      const ref = await recorder.captureRecorderScreenshot(config.recordingId);
      save(`screenshot-${command.id}.json`, { ref });
      const shot = await contents.debugger.sendCommand("Page.captureScreenshot", { format: "png" });
      writeFileSync(join(config.evidence, `page-${command.id}.png`), Buffer.from(shot.data, "base64"));
    }
    const captured = contents && !contents.isDestroyed() ? await evaluate("({recorder:window.__qaRecorderGetState(),clicks:window.fixtureClicks||0,title:document.title})") : null;
    save(`reply-${command.id}.json`, { ...captured, host: recorder.getRecorderWindowState(config.recordingId) });
  }
} catch (error) {
  save("harness-error.json", { message: error.message, stack: error.stack });
  process.exitCode = 1;
} finally {
  if (recorder) await recorder.closeRecorderSession(config.recordingId, { notifyBackend: false });
  app.exit(process.exitCode || 0);
}
}
// The entry module must finish evaluating before Electron can emit ready.
void run();
