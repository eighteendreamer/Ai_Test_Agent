/**
 * 录制域 API 封装（P0-10）。
 *
 * 后端契约：src/api/routes/recordings.py
 *   GET  /api/v1/recordings?project_id=&limit=&offset=   → { items, count }
 *   GET  /api/v1/recordings/{id}                         → 会话 + 事件流
 *   POST /api/v1/recordings/{id}/control                 → 控制条指令
 */

export interface RecordingPublic {
  id: string;
  project_id: string;
  name: string;
  entry_url: string;
  driver_kind: string;
  status: string;
  session_id: string | null;
  approval_id: string | null;
  step_count: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  ended_at: string | null;
  finalize_metrics: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface RecorderEvent {
  seq: number;
  type: string;
  timestamp: string;
  page: { url?: string; title?: string } & Record<string, unknown>;
  target: {
    locators?: {
      text?: string | null;
      role_name?: { role?: string; name?: string } | null;
      css?: string | null;
      id?: string | null;
    } & Record<string, unknown>;
    tag?: string;
  } & Record<string, unknown> | null;
  pixel: Record<string, unknown> | null;
  value: unknown;
  page_effect: Record<string, unknown>;
  screenshot_ref: string | null;
}

export interface RecordingDetail extends RecordingPublic {
  events: RecorderEvent[];
}

export async function listRecordings(limit = 20): Promise<RecordingPublic[]> {
  const response = await fetch(`/api/v1/recordings?limit=${limit}`);
  if (!response.ok) {
    throw new Error(`list recordings failed: HTTP ${response.status}`);
  }
  const body = (await response.json()) as { items?: RecordingPublic[] };
  return body.items ?? [];
}

export async function getRecordingDetail(recordingId: string): Promise<RecordingDetail> {
  const response = await fetch(`/api/v1/recordings/${encodeURIComponent(recordingId)}`);
  if (!response.ok) {
    throw new Error(`get recording failed: HTTP ${response.status}`);
  }
  return (await response.json()) as RecordingDetail;
}

/** 事件条目主标签：优先语义名（text/role name），退化到 css/id。 */
export function recorderEventLabel(event: RecorderEvent): string {
  const locators = event.target?.locators ?? {};
  const text = String(locators.text || "").trim();
  if (text) {
    return text.slice(0, 48);
  }
  const roleName = locators.role_name;
  if (roleName && typeof roleName === "object") {
    const name = String((roleName as { name?: string }).name || "").trim();
    if (name) {
      return name.slice(0, 48);
    }
  }
  const css = String(locators.css || "").trim();
  if (css) {
    return css.slice(0, 48);
  }
  const id = String(locators.id || "").trim();
  if (id) {
    return `#${id}`;
  }
  return String(event.page?.url || event.page?.title || "").slice(0, 64);
}

/** 步骤是否计入用户操作（page_scan 等辅助事件不进时间线主列表）。 */
export function isUserActionEvent(event: RecorderEvent): boolean {
  return [
    "click",
    "dblclick",
    "fill",
    "key",
    "submit",
    "scroll",
    "navigate",
    "file_change",
  ].includes(event.type);
}
