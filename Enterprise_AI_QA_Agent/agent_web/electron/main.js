import { app, BrowserWindow, ipcMain, Menu, Notification, shell } from "electron";
import { createReadStream, existsSync, mkdirSync, rmSync, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import removeMarkdown from "remove-markdown";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const appRoot = resolve(__dirname, "..");
const rendererRoot = join(appRoot, "dist");
const iconPath = join(appRoot, "desktop-assets", process.platform === "win32" ? "logo.ico" : "logo.png");
const APP_NAME = "御策天检";
// Windows 通知/任务栏共用同一个 AppUserModelId。
// 使用英文 ID 避免中文编码问题；通知显示的中文名由快捷方式 description 提供。
const APP_USER_MODEL_ID = "御策天检";
const backendOrigin = process.env.QA_AGENT_API_ORIGIN || "http://127.0.0.1:1032";
const liveRendererOrigin = (process.env.QA_AGENT_RENDERER_ORIGIN || "").trim().replace(/\/$/, "");
const desktopDebugEnabled = !app.isPackaged || process.env.QA_AGENT_DESKTOP_DEBUG === "1";

let mainWindow = null;
let staticServer = null;

function ensureWindowsShortcut() {
  if (process.platform !== "win32") return;

  const programsPath = join(app.getPath("appData"), "Microsoft", "Windows", "Start Menu", "Programs");
  // 使用英文文件名避免 Electron shell API 的 Unicode 路径问题；
  // 通知中心显示的应用名由 description / AppUserModelId 决定，不受文件名影响。
  const shortcutPath = join(programsPath, "YuceTianjian.lnk");
  const legacyShortcutPath = join(programsPath, `${APP_NAME}.lnk`);

  try {
    mkdirSync(programsPath, { recursive: true });
    // 清理旧的中文/乱码快捷方式，避免冲突。
    if (legacyShortcutPath !== shortcutPath && existsSync(legacyShortcutPath)) {
      rmSync(legacyShortcutPath);
      console.log("[Desktop] Removed legacy shortcut:", legacyShortcutPath);
    }
    shell.writeShortcutLink(shortcutPath, "replace", {
      target: process.execPath,
      args: process.argv.slice(1).join(" "),
      cwd: process.cwd(),
      description: APP_NAME,
      icon: existsSync(iconPath) ? `${iconPath},0` : undefined,
      appUserModelId: APP_USER_MODEL_ID,
    });
    console.log("[Desktop] Start menu shortcut ensured:", shortcutPath);
  } catch (err) {
    console.warn("[Desktop] Failed to create start menu shortcut:", err);
  }
}

const mimeTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".gif", "image/gif"],
  [".ico", "image/x-icon"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

function safeRendererPath(urlPath) {
  const decodedPath = decodeURIComponent(urlPath.split("?")[0] || "/");
  const normalizedPath = normalize(decodedPath).replace(/^(\.\.[/\\])+/, "");
  const candidate = join(rendererRoot, normalizedPath);
  const resolvedCandidate = resolve(candidate);

  if (!resolvedCandidate.startsWith(resolve(rendererRoot))) {
    return join(rendererRoot, "index.html");
  }

  if (existsSync(resolvedCandidate) && statSync(resolvedCandidate).isFile()) {
    return resolvedCandidate;
  }

  if (existsSync(resolvedCandidate) && statSync(resolvedCandidate).isDirectory()) {
    const directoryIndex = join(resolvedCandidate, "index.html");
    if (existsSync(directoryIndex) && statSync(directoryIndex).isFile()) {
      return directoryIndex;
    }
  }

  const htmlCandidate = `${resolvedCandidate}.html`;
  if (existsSync(htmlCandidate) && statSync(htmlCandidate).isFile()) {
    return htmlCandidate;
  }

  return join(rendererRoot, "index.html");
}

function writeStaticResponse(response, filePath) {
  const contentType = mimeTypes.get(extname(filePath).toLowerCase()) || "application/octet-stream";
  response.writeHead(200, {
    "content-type": contentType,
    "cache-control": filePath.endsWith("index.html") ? "no-cache" : "public, max-age=31536000",
  });
  createReadStream(filePath).pipe(response);
}

function proxyApiRequest(clientRequest, clientResponse) {
  const targetUrl = new URL(clientRequest.url || "/", backendOrigin);
  const transport = targetUrl.protocol === "https:" ? httpsRequest : httpRequest;
  const upstreamRequest = transport(
    targetUrl,
    {
      method: clientRequest.method,
      headers: {
        ...clientRequest.headers,
        host: targetUrl.host,
        origin: backendOrigin,
      },
    },
    (upstreamResponse) => {
      clientResponse.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      upstreamResponse.pipe(clientResponse);
    },
  );

  upstreamRequest.on("error", (error) => {
    clientResponse.writeHead(502, { "content-type": "application/json; charset=utf-8" });
    clientResponse.end(JSON.stringify({ detail: `Desktop proxy failed: ${error.message}` }));
  });

  clientRequest.pipe(upstreamRequest);
}

function startStaticServer() {
  if (!existsSync(join(rendererRoot, "index.html"))) {
    throw new Error("Renderer build not found. Run `npm run build` first.");
  }

  staticServer = createServer((request, response) => {
    const requestUrl = request.url || "/";
    const requestPath = new URL(requestUrl, "http://desktop.local").pathname;
    if (requestPath === "/docs/home" || requestPath === "/docs/home.html") {
      response.writeHead(302, { location: "/home" });
      response.end();
      return;
    }

    if (requestUrl.startsWith("/api/")) {
      proxyApiRequest(request, response);
      return;
    }

    writeStaticResponse(response, safeRendererPath(requestUrl));
  });

  return new Promise((resolveServer, rejectServer) => {
    staticServer.once("error", rejectServer);
    staticServer.listen(0, "127.0.0.1", () => {
      const address = staticServer.address();
      if (!address || typeof address === "string") {
        rejectServer(new Error("Failed to allocate desktop renderer port."));
        return;
      }
      resolveServer(`http://127.0.0.1:${address.port}`);
    });
  });
}

function toggleDetachedDevTools(webContents) {
  if (webContents.isDevToolsOpened()) {
    webContents.closeDevTools();
    return;
  }

  webContents.openDevTools({ mode: "detach", activate: true });
}

function normalizeNotificationPayload(payload) {
  const record = payload && typeof payload === "object" ? payload : {};
  const title = typeof record.title === "string" ? record.title.trim().slice(0, 120) : "";
  const rawBody = typeof record.body === "string" ? record.body.trim() : "";
  const plainBody = rawBody ? removeMarkdown(rawBody, { gfm: true, useImgAltText: true }).trim() : "";
  const body = plainBody.slice(0, 500);
  const tag = typeof record.tag === "string" ? record.tag.trim().slice(0, 120) : undefined;

  if (!title) {
    return null;
  }

  return {
    title,
    body,
    tag,
    silent: Boolean(record.silent),
  };
}

function focusMainWindow() {
  if (!mainWindow) {
    return;
  }

  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
}

function registerDesktopIpc() {
  ipcMain.handle("desktop:set-zoom-factor", (event, factor) => {
    const numericFactor = Number(factor);
    const normalizedFactor = Number.isFinite(numericFactor)
      ? Math.min(1.5, Math.max(0.75, numericFactor))
      : 1;
    event.sender.setZoomFactor(normalizedFactor);
    return normalizedFactor;
  });

  ipcMain.handle("desktop:notify", (_event, payload) => {
    const normalized = normalizeNotificationPayload(payload);
    if (!normalized || !Notification.isSupported()) {
      return false;
    }

    // 不设置 icon，避免 Windows 通知左侧显示大图标。
    // 顶部“御策天检”旁边的小图标由开始菜单快捷方式提供。
    const notification = new Notification({
      title: normalized.title,
      body: normalized.body,
      tag: normalized.tag,
      silent: normalized.silent,
    });
    notification.on("click", focusMainWindow);
    notification.show();
    return true;
  });
}

async function resolveRendererOrigin() {
  if (liveRendererOrigin) {
    console.log("[Desktop] Using live Vite renderer:", liveRendererOrigin);
    return liveRendererOrigin;
  }
  console.log("[Desktop] Serving static renderer. For HMR use `npm run desktop:dev`.");
  return startStaticServer();
}

async function createMainWindow() {
  Menu.setApplicationMenu(null);

  const rendererOrigin = await resolveRendererOrigin();
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1100,
    minHeight: 720,
    title: APP_NAME,
    icon: existsSync(iconPath) ? iconPath : undefined,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      devTools: true,
      nodeIntegration: false,
      preload: join(__dirname, "preload.cjs"),
      sandbox: true,
    },
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadURL(rendererOrigin);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("before-input-event", (event, input) => {
    const key = input.key.toLowerCase();
    if (desktopDebugEnabled && (input.key === "F12" || (input.control && input.shift && key === "i"))) {
      toggleDetachedDevTools(mainWindow.webContents);
      event.preventDefault();
    }
  });

  if (desktopDebugEnabled) {
    mainWindow.webContents.once("did-finish-load", () => {
      mainWindow.webContents.openDevTools({ mode: "detach", activate: true });
    });
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  if (process.platform === "win32") {
    app.setAppUserModelId(APP_USER_MODEL_ID);
    ensureWindowsShortcut();
  }
  registerDesktopIpc();
  return createMainWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  staticServer?.close();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createMainWindow();
  }
});
