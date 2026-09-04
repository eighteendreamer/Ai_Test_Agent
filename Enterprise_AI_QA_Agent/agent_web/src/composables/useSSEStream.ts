import type { ExecutionEvent } from "../types";
import { api } from "../services/api";

const EVENT_RECONNECT_DELAY_MS = 3000;
const RECEIVED_EVENT_ID_LIMIT = 500;

export interface SSEStreamCallbacks {
  onEvent: (event: ExecutionEvent) => void;
  onConnect: () => void;
  onError: (error: string) => void;
}

export class SSEStreamManager {
  private _source: EventSource | null = null;
  private _sessionId = "";
  private _reconnectTimer: number | null = null;
  private _receivedIds: string[] = [];
  private _cursorBySession: Record<string, string> = {};
  private _callbacks: SSEStreamCallbacks | null = null;

  get source(): EventSource | null {
    return this._source;
  }

  get connectedSessionId(): string {
    return this._sessionId;
  }

  get reconnectTimer(): number | null {
    return this._reconnectTimer;
  }

  set reconnectTimer(value: number | null) {
    this._reconnectTimer = value;
  }

  get receivedIds(): string[] {
    return this._receivedIds;
  }

  set receivedIds(value: string[]) {
    this._receivedIds = value;
  }

  get cursorBySession(): Record<string, string> {
    return this._cursorBySession;
  }

  set cursorBySession(value: Record<string, string>) {
    this._cursorBySession = value;
  }

  bind(callbacks: SSEStreamCallbacks): void {
    this._callbacks = callbacks;
  }

  connect(sessionId: string, force = false): void {
    if (!sessionId) return;
    if (this._source && this._sessionId === sessionId && !force) return;

    this.disconnect();

    const lastEventId = this._cursorBySession[sessionId] || "";
    const source = api.connectEvents(sessionId, (event) => {
      if (!this.rememberIncomingEvent(event)) return;
      this._callbacks?.onEvent(event);
    }, lastEventId);

    source.onopen = () => {
      if (this._source !== source) return;
      this._callbacks?.onConnect();
    };

    source.onerror = () => {
      if (this._source !== source) return;
      source.close();
      this._source = null;
      this._callbacks?.onError("事件流已断开，正在尝试重新连接。");
      this.scheduleReconnect(sessionId);
    };

    this._source = source;
    this._sessionId = sessionId;
  }

  ensureConnected(sessionId: string | null | undefined): void {
    if (!sessionId) return;
    if (!this._source || this._sessionId !== sessionId || this._source.readyState === EventSource.CLOSED) {
      this.connect(sessionId, true);
    }
  }

  disconnect(): void {
    if (this._reconnectTimer !== null) {
      window.clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    this._source?.close();
    this._source = null;
    this._sessionId = "";
  }

  scheduleReconnect(sessionId: string): void {
    if (!sessionId || this._reconnectTimer !== null) return;
    this._reconnectTimer = window.setTimeout(() => {
      this._reconnectTimer = null;
      this.connect(sessionId, true);
    }, EVENT_RECONNECT_DELAY_MS);
  }

  rememberIncomingEvent(event: ExecutionEvent): boolean {
    const eventId = String(event.id || "").trim();
    if (!eventId) return true;
    if (this._receivedIds.includes(eventId)) return false;
    this._receivedIds = [...this._receivedIds, eventId].slice(-RECEIVED_EVENT_ID_LIMIT);
    this._cursorBySession = { ...this._cursorBySession, [event.session_id]: eventId };
    return true;
  }
}
