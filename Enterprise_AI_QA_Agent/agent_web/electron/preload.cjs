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
});
