/**
 * SSE 录制事件 → 桌面端联动（方案 4.2⑤，P0-10）。
 *
 * recorder.launch_requested：审批通过后后端创建录制会话并推此事件，
 * 桌面端自动调起 Electron 录制窗口（recorder:create-window IPC），
 * 用户零手动启动。非桌面端（纯浏览器访问）忽略——embedded 驱动仅桌面可用。
 */

const launchedRecordingIds = new Set<string>();

function trimValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export async function handleRecorderSseEvent(event: { type?: string; payload?: Record<string, unknown> }): Promise<void> {
  if (event.type !== "recorder.launch_requested") {
    return;
  }

  const recordingId = trimValue(event.payload?.recording_id);
  const entryUrl = trimValue(event.payload?.entry_url);
  const driverKind = trimValue(event.payload?.driver_kind) || "embedded";
  if (!recordingId || !entryUrl) {
    console.warn("[recorder] launch_requested event missing recording_id/entry_url, ignored");
    return;
  }
  if (driverKind !== "embedded") {
    // cdp-attach / playwright-managed 由服务端自驱（P1），桌面端不弹内嵌窗口。
    return;
  }
  if (launchedRecordingIds.has(recordingId)) {
    return;
  }

  const bridge = window.qaAgentDesktop;
  if (!bridge?.isDesktop || !bridge.recorder?.createWindow) {
    console.warn("[recorder] desktop bridge unavailable; embedded recording window cannot open");
    return;
  }

  launchedRecordingIds.add(recordingId);
  try {
    await bridge.recorder.createWindow({ recordingId, entryUrl });
    console.log("[recorder] recording window opened for", recordingId);
  } catch (error) {
    launchedRecordingIds.delete(recordingId);
    console.error("[recorder] failed to open recording window:", error);
  }
}

/** 测试用：重置去重集合。 */
export function resetLaunchedRecordingIds(): void {
  launchedRecordingIds.clear();
}
