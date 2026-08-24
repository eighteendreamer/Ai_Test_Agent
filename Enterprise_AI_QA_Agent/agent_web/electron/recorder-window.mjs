/**
 * UI 录制窗口（方案 5 embedded 驱动的 Electron 侧实现，P0-9）。
 *
 * 结构：BrowserWindow 上半部加载控制条路由（/recorder-window），下半部挂
 * WebContentsView 加载目标产品 URL（partition persist:recorder 持久登录态）。
 *
 * 桥接三通道（与 Agent_Server EmbeddedBridge 对齐）：
 *   上行  POST /api/v1/recordings/{id}/events:batch     事件批量（2s/20条攒批）
 *   下行  GET  /api/v1/recordings/{id}/commands          指令 long-poll
 *   握手  POST /api/v1/recordings/{id}/attach-registry   窗口+注入完成后登记
 *   截图  POST /api/v1/recordings/{id}/screenshots       multipart
 *
 * 注入：webContents.debugger（CDP 1.3）→ Page.addScriptToEvaluateOnNewDocument
 * （recorder.js 唯一源在后端 GET /api/v1/recordings/recorder.js，启动时拉取）
 * → Runtime.addBinding("__qaRecordEmit") 收事件。
 */
import { BrowserWindow, WebContentsView } from "electron";

export const CONTROL_BAR_HEIGHT = 56;

const EMIT_BINDING = "__qaRecordEmit";
const EVENT_BATCH_MAX = 20;
const EVENT_FLUSH_INTERVAL_MS = 2000;
const EVENT_BUFFER_HARD_LIMIT = 2000;
const COMMAND_WAIT_SECONDS = 25;
const HTTP_TIMEOUT_MS = 15000;

/** @type {Map<string, RecorderSessionState>} */
const sessions = new Map();

let backendOrigin = "";
let rendererOrigin = "";

class RecorderSessionState {
  constructor(recordingId) {
    this.recordingId = recordingId;
    /** @type {BrowserWindow | null} */
    this.win = null;
    /** @type {import("electron").WebContentsView | null} */
    this.view = null;
    this.debuggerAttached = false;
    this.eventBuffer = [];
    this.droppedMalformed = 0;
    this.forwardedCount = 0;
    this.flushTimer = null;
    this.pollAborted = false;
    this.closedByCommand = false; // 收到后端 close 指令的关窗不再补发 stop
    this.flushing = false;
  }
}

export function configureRecorderBridge(options) {
  backendOrigin = String(options?.backendOrigin || "").replace(/\/$/, "");
  rendererOrigin = String(options?.rendererOrigin || "").replace(/\/$/, "");
}

function sessionOf(recordingId) {
  return sessions.get(String(recordingId || ""));
}

function log(state, message, ...args) {
  console.log(`[Recorder:${state.recordingId.slice(0, 8)}] ${message}`, ...args);
}

// ---------------------------------------------------------------- HTTP 通道

async function backendFetch(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);
  try {
    const response = await fetch(`${backendOrigin}${path}`, {
      ...init,
      signal: controller.signal,
    });
    return response;
  } finally {
    clearTimeout(timer);
  }
}

let recorderScriptCache = null;

async function fetchRecorderScript() {
  if (recorderScriptCache) {
    return recorderScriptCache;
  }
  const response = await backendFetch("/api/v1/recordings/recorder.js");
  if (!response.ok) {
    throw new Error(`fetch recorder.js failed: ${response.status}`);
  }
  recorderScriptCache = await response.text();
  if (!recorderScriptCache.includes("__qaRecorderInstalled")) {
    throw new Error("recorder.js payload looks invalid");
  }
  return recorderScriptCache;
}

async function postAttachRegistry(state) {
  const response = await backendFetch(`/api/v1/recordings/${state.recordingId}/attach-registry`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      capabilities: { driver: "embedded", platform: process.platform },
    }),
  });
  if (!response.ok) {
    throw new Error(`attach-registry failed: ${response.status}`);
  }
  log(state, "registered to backend bridge");
}

async function flushEventBuffer(state, { force = false } = {}) {
  if (state.flushing || state.eventBuffer.length === 0) {
    return;
  }
  if (!force && state.eventBuffer.length < EVENT_BATCH_MAX) {
    return;
  }
  state.flushing = true;
  const batch = state.eventBuffer.splice(0, EVENT_BATCH_MAX);
  try {
    const response = await backendFetch(
      `/api/v1/recordings/${state.recordingId}/events:batch`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          events: batch,
          client_batch_id: `${state.recordingId}-${batch[0]?.seq ?? 0}`,
        }),
      },
    );
    if (!response.ok) {
      // 失败放回缓冲头部（保序），硬上限保护内存。
      state.eventBuffer.unshift(...batch);
      log(state, `events:batch failed: ${response.status}, buffered=${state.eventBuffer.length}`);
    } else {
      state.forwardedCount += batch.length;
    }
  } catch (error) {
    state.eventBuffer.unshift(...batch);
    log(state, "events:batch error:", error?.message || error);
  } finally {
    state.flushing = false;
    if (state.eventBuffer.length > EVENT_BUFFER_HARD_LIMIT) {
      const dropped = state.eventBuffer.length - EVENT_BUFFER_HARD_LIMIT;
      state.eventBuffer.splice(EVENT_BUFFER_HARD_LIMIT);
      log(state, `event buffer hard limit exceeded, dropped oldest ${dropped}`);
    }
  }
}

function startEventFlushTimer(state) {
  state.flushTimer = setInterval(() => {
    void flushEventBuffer(state);
  }, EVENT_FLUSH_INTERVAL_MS);
}

function handleRecorderEvent(state, payloadJson) {
  let event;
  try {
    event = JSON.parse(payloadJson);
  } catch {
    state.droppedMalformed += 1;
    return;
  }
  if (!event || typeof event !== "object" || typeof event.type !== "string") {
    state.droppedMalformed += 1;
    return;
  }
  state.eventBuffer.push(event);
  if (state.eventBuffer.length >= EVENT_BATCH_MAX) {
    void flushEventBuffer(state, { force: true });
  }
}

// ---------------------------------------------------------------- 指令轮询

async function evaluateInView(state, expression) {
  if (!state.view || !state.debuggerAttached) {
    return null;
  }
  try {
    const result = await state.view.webContents.debugger.sendCommand(
      "Runtime.evaluate",
      { expression, returnByValue: true },
    );
    return result?.result?.value ?? null;
  } catch (error) {
    log(state, "Runtime.evaluate failed:", error?.message || error);
    return null;
  }
}

async function captureScreenshotToBackend(state) {
  if (!state.view || !state.debuggerAttached) {
    return null;
  }
  try {
    const shot = await state.view.webContents.debugger.sendCommand(
      "Page.captureScreenshot",
      { format: "png" },
    );
    if (!shot?.data) {
      return null;
    }
    const bytes = Buffer.from(shot.data, "base64");
    const form = new FormData();
    form.append("file", new Blob([bytes], { type: "image/png" }), "frame.png");
    const response = await backendFetch(
      `/api/v1/recordings/${state.recordingId}/screenshots`,
      { method: "POST", body: form },
    );
    if (!response.ok) {
      log(state, `screenshot upload failed: ${response.status}`);
      return null;
    }
    const body = await response.json();
    return body?.ref ?? null;
  } catch (error) {
    log(state, "screenshot capture failed:", error?.message || error);
    return null;
  }
}

async function applyCommand(state, command) {
  const kind = command?.kind || command?.command;
  const payload = command?.payload || {};
  if (kind === "navigate") {
    log(state, "navigate ->", payload.url);
    if (state.view && payload.url) {
      state.view.webContents.loadURL(String(payload.url)).catch(() => {});
    }
    return;
  }
  if (kind === "set_capture_enabled") {
    const enabled = payload.enabled === true || payload.enabled === "true";
    log(state, "set_capture_enabled ->", enabled);
    await evaluateInView(
      state,
      `window.__qaRecorderSetEnabled && window.__qaRecorderSetEnabled(${enabled});`,
    );
    return;
  }
  if (kind === "close") {
    log(state, "close command from backend");
    state.closedByCommand = true;
    closeRecorderSession(state.recordingId, { notifyBackend: false });
    return;
  }
  log(state, "unknown command ignored:", kind);
}

async function commandPollLoop(state) {
  while (!state.pollAborted) {
    try {
      const response = await backendFetch(
        `/api/v1/recordings/${state.recordingId}/commands?wait_seconds=${COMMAND_WAIT_SECONDS}`,
      );
      if (!response.ok) {
        if (response.status === 404) {
          log(state, "commands poll: session unknown, stop polling");
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
        continue;
      }
      const body = await response.json();
      for (const command of body?.commands || []) {
        if (state.pollAborted) {
          return;
        }
        await applyCommand(state, command);
      }
    } catch (error) {
      if (state.pollAborted) {
        return;
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

// ---------------------------------------------------------------- 窗口管理

function syncBrowserViewBounds(state) {
  if (!state.win || !state.view || state.win.isDestroyed()) {
    return;
  }
  const [width, height] = state.win.getContentSize();
  state.view.setBounds({
    x: 0,
    y: CONTROL_BAR_HEIGHT,
    width: Math.max(0, width),
    height: Math.max(0, height - CONTROL_BAR_HEIGHT),
  });
}

async function attachDebuggerWithScript(state, scriptSource) {
  const dbg = state.view.webContents.debugger;
  if (!dbg.isAttached()) {
    dbg.attach("1.3");
    state.debuggerAttached = true;
  }
  dbg.on("message", (_event, method, params) => {
    if (method === "Runtime.bindingCalled" && params?.name === EMIT_BINDING) {
      handleRecorderEvent(state, String(params.payload || "{}"));
    }
  });
  await dbg.sendCommand("Page.enable");
  await dbg.sendCommand("Runtime.enable");
  // 新文档（含导航）自动注入 recorder.js；binding 先注册，脚本发出的事件不丢。
  await dbg.sendCommand("Runtime.addBinding", { name: EMIT_BINDING });
  await dbg.sendCommand("Page.addScriptToEvaluateOnNewDocument", { source: scriptSource });
}

async function teardownSession(state, { notifyBackend }) {
  state.pollAborted = true;
  if (state.flushTimer) {
    clearInterval(state.flushTimer);
    state.flushTimer = null;
  }
  // 尽力冲刷缓冲（close 指令路径由后端主导，destroy 已声明丢弃则不补投）。
  await flushEventBuffer(state, { force: true }).catch(() => {});
  if (notifyBackend && !state.closedByCommand) {
    // 用户直接关窗：语义视为 stop（固化已录数据），fire-and-forget。
    void backendFetch(`/api/v1/recordings/${state.recordingId}/control`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "stop", reason: "recorder window closed" }),
    }).catch(() => {});
  }
  if (state.view && !state.view.webContents.isDestroyed()) {
    try {
      if (state.view.webContents.debugger.isAttached()) {
        state.view.webContents.debugger.detach();
      }
    } catch {
      // webContents 可能已销毁
    }
  }
  state.debuggerAttached = false;
  sessions.delete(state.recordingId);
}

export async function createRecorderWindow(payload = {}) {
  const recordingId = String(payload.recordingId || "").trim();
  const entryUrl = String(payload.entryUrl || "").trim();
  if (!recordingId || !entryUrl) {
    throw new Error("recorder:create-window requires recordingId and entryUrl");
  }
  if (!rendererOrigin) {
    throw new Error("recorder bridge not configured (rendererOrigin missing)");
  }

  const existing = sessionOf(recordingId);
  if (existing?.win && !existing.win.isDestroyed()) {
    existing.win.show();
    existing.win.focus();
    return { recordingId, reused: true };
  }
  if (existing) {
    sessions.delete(recordingId);
  }

  const state = new RecorderSessionState(recordingId);
  sessions.set(recordingId, state);

  const controlUrl = new URL("/recorder-window", rendererOrigin);
  controlUrl.searchParams.set("recording_id", recordingId);
  controlUrl.searchParams.set("entry_url", entryUrl);

  state.win = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 960,
    minHeight: 640,
    title: `御策天检 · UI 录制${payload.name ? ` · ${payload.name}` : ""}`,
    autoHideMenuBar: true,
  });
  state.win.setMenuBarVisibility(false);

  state.view = new WebContentsView({
    webPreferences: {
      // 独立持久 partition：与主窗口登录态隔离，录制域自持登录（方案 9.1）。
      partition: "persist:recorder",
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  state.win.contentView.addChildView(state.view);
  syncBrowserViewBounds(state);
  state.win.on("resize", () => syncBrowserViewBounds(state));
  state.win.on("maximize", () => syncBrowserViewBounds(state));
  state.win.on("unmaximize", () => syncBrowserViewBounds(state));

  state.win.on("closed", () => {
    void teardownSession(state, { notifyBackend: true });
  });

  // 控制条先渲染（不阻塞），产品页注入链路随后进行。
  state.win.loadURL(controlUrl.toString()).catch((error) => {
    log(state, "control bar load failed:", error?.message || error);
  });

  try {
    const script = await fetchRecorderScript();
    await attachDebuggerWithScript(state, script);
    await state.view.webContents.loadURL(entryUrl);
    await postAttachRegistry(state);
  } catch (error) {
    log(state, "bridge setup failed:", error?.message || error);
    await teardownSession(state, { notifyBackend: true });
    if (state.win && !state.win.isDestroyed()) {
      state.win.close();
    }
    throw error;
  }

  startEventFlushTimer(state);
  void commandPollLoop(state);
  log(state, "recording window ready, entry:", entryUrl);
  return { recordingId, reused: false };
}

export function navigateRecorder(recordingId, url) {
  const state = sessionOf(recordingId);
  if (!state?.view) {
    return false;
  }
  state.view.webContents.loadURL(String(url || "")).catch(() => {});
  return true;
}

export async function setRecorderCapture(recordingId, enabled) {
  const state = sessionOf(recordingId);
  if (!state) {
    return false;
  }
  await evaluateInView(
    state,
    `window.__qaRecorderSetEnabled && window.__qaRecorderSetEnabled(${enabled === true});`,
  );
  return true;
}

export async function captureRecorderScreenshot(recordingId) {
  const state = sessionOf(recordingId);
  if (!state) {
    return null;
  }
  return captureScreenshotToBackend(state);
}

export async function closeRecorderSession(recordingId, { notifyBackend = true } = {}) {
  const state = sessionOf(recordingId);
  if (!state) {
    return false;
  }
  await teardownSession(state, { notifyBackend });
  if (state.win && !state.win.isDestroyed()) {
    state.win.close();
  }
  return true;
}

export function getRecorderWindowState(recordingId) {
  const state = sessionOf(recordingId);
  if (!state) {
    return null;
  }
  const currentUrl =
    state.view && !state.view.webContents.isDestroyed()
      ? state.view.webContents.getURL()
      : "";
  return {
    recordingId: state.recordingId,
    attached: state.debuggerAttached,
    currentUrl,
    bufferedEvents: state.eventBuffer.length,
    forwardedEvents: state.forwardedCount,
    droppedMalformed: state.droppedMalformed,
  };
}
