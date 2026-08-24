const { contextBridge, ipcRenderer, webFrame } = require("electron");

function normalizeZoomFactor(factor) {
  const numericFactor = Number(factor);
  return Number.isFinite(numericFactor)
    ? Math.min(1.5, Math.max(0.75, numericFactor))
    : 1;
}

contextBridge.exposeInMainWorld("qaAgentDesktop", {
  isDesktop: true,
  notify(payload) {
    return ipcRenderer.invoke("desktop:notify", payload);
  },
  setZoomFactor(factor) {
    const normalizedFactor = normalizeZoomFactor(factor);
    if (webFrame?.setZoomFactor) {
      webFrame.setZoomFactor(normalizedFactor);
      return Promise.resolve(normalizedFactor);
    }
    return ipcRenderer.invoke("desktop:set-zoom-factor", normalizedFactor);
  },
  openFlowWindow(payload) {
    return ipcRenderer.invoke("desktop:open-flow-window", payload || {});
  },
  recorder: {
    /** 审批通过/SSE 通知后调起录制窗口（自动注入 + 登记后端）。 */
    createWindow(payload) {
      return ipcRenderer.invoke("recorder:create-window", payload || {});
    },
    navigate(recordingId, url) {
      return ipcRenderer.invoke("recorder:navigate", { recordingId, url });
    },
    attachDebugger(recordingId) {
      return ipcRenderer.invoke("recorder:attach-debugger", { recordingId });
    },
    setCapture(recordingId, enabled) {
      return ipcRenderer.invoke("recorder:set-capture", { recordingId, enabled });
    },
    captureScreenshot(recordingId) {
      return ipcRenderer.invoke("recorder:capture", { recordingId });
    },
    close(recordingId) {
      return ipcRenderer.invoke("recorder:close", { recordingId });
    },
    getState(recordingId) {
      return ipcRenderer.invoke("recorder:get-state", { recordingId });
    },
  },
});
