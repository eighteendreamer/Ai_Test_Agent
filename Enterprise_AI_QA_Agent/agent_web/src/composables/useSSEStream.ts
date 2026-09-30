import type { ExecutionEvent } from "../types";
import { api } from "../services/api";

export const SSE_RECONNECTING_ERROR = "事件流已断开，正在尝试重新连接。";
export const SSE_RECONNECT_EXHAUSTED_ERROR = "事件流连续断开，已停止自动重连。请手动重试或刷新页面。";

export interface SSEStreamConfig {
  reconnectDelayMs: number;
  maxReconnectDelayMs: number;
  maxReconnectAttempts: number;
  receivedIdLimit: number;
}

const DEFAULT_SSE_CONFIG: SSEStreamConfig = {
  reconnectDelayMs: 1500,
  maxReconnectDelayMs: 30000,
  maxReconnectAttempts: 8,
  receivedIdLimit: 5000,
};

export interface SSEStreamCallbacks {
  onEvent: (event: ExecutionEvent) => void;
  onConnect: () => void;
  onError: (error: string) => void;
}

export class SSEStreamManager {
  private _source: EventSource | null = null;
  private _sessionId = "";
  private _reconnectTimer: number | null = null;
  private _reconnectFailures = 0;
  private _receivedIds = new Set<string>();
  private _receivedSessionId = "";
  private _cursorBySession: Record<string, string> = {};
  private _callbacks: SSEStreamCallbacks | null = null;
  private _config: SSEStreamConfig;

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
    return [...this._receivedIds];
  }

  set receivedIds(value: string[]) {
    this._receivedIds = new Set(value.slice(-this._config.receivedIdLimit));
  }

  get cursorBySession(): Record<string, string> {
    return this._cursorBySession;
  }

  set cursorBySession(value: Record<string, string>) {
    this._cursorBySession = value;
  }

  constructor(config: Partial<SSEStreamConfig> = {}) {
    this._config = { ...DEFAULT_SSE_CONFIG, ...config };
  }

  bind(callbacks: SSEStreamCallbacks): void {
    this._callbacks = callbacks;
  }

  connect(sessionId: string, force = false): void {
    this._connect(sessionId, force, false);
  }

  private _connect(sessionId: string, force: boolean, retry: boolean): void {
    if (!sessionId) return;
    if (this._source && this._sessionId === sessionId && !force) return;
    if (!retry || this._sessionId !== sessionId) this._reconnectFailures = 0;

    this._closeSource();
    if (this._receivedSessionId !== sessionId) {
      this._receivedIds.clear();
      this._receivedSessionId = sessionId;
    }

    const lastEventId = this._cursorBySession[sessionId] || "";
    const source = api.connectEvents(sessionId, (event) => {
      if (!this.rememberIncomingEvent(event)) return;
      this._callbacks?.onEvent(event);
    }, lastEventId, (error) => {
      if (this._source !== source) return;
      this._callbacks?.onError(error);
    });

    source.onopen = () => {
      if (this._source !== source) return;
      this._reconnectFailures = 0;
      this._callbacks?.onConnect();
    };

    source.onerror = () => {
      if (this._source !== source) return;
      source.close();
      this._source = null;
      this._reconnectFailures += 1;
      if (this._reconnectFailures >= this._config.maxReconnectAttempts) {
        this._callbacks?.onError(SSE_RECONNECT_EXHAUSTED_ERROR);
      } else {
        this._callbacks?.onError(SSE_RECONNECTING_ERROR);
        this.scheduleReconnect(sessionId);
      }
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
    this._closeSource();
    this._reconnectFailures = 0;
    this._receivedIds.clear();
    this._receivedSessionId = "";
  }

  private _closeSource(): void {
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
    if (this._sessionId !== sessionId) this._reconnectFailures = 0;
    if (this._reconnectFailures >= this._config.maxReconnectAttempts) return;
    const delay = Math.min(
      this._config.maxReconnectDelayMs,
      this._config.reconnectDelayMs * 2 ** Math.max(0, this._reconnectFailures - 1),
    );
    this._reconnectTimer = window.setTimeout(() => {
      this._reconnectTimer = null;
      this._connect(sessionId, true, true);
    }, delay);
  }

  rememberIncomingEvent(event: ExecutionEvent): boolean {
    return this._rememberEvent(event, true);
  }

  rememberReceiptEvent(event: ExecutionEvent): boolean {
    return this._rememberEvent(event, false);
  }

  private _rememberEvent(event: ExecutionEvent, advanceCursor: boolean): boolean {
    const eventId = String(event.id || "").trim();
    if (!eventId) return true;
    if (this._receivedSessionId !== event.session_id) {
      this._receivedIds.clear();
      this._receivedSessionId = event.session_id;
    }
    if (advanceCursor) {
      this._cursorBySession = { ...this._cursorBySession, [event.session_id]: eventId };
    }
    if (this._receivedIds.has(eventId)) return false;
    this._receivedIds.add(eventId);
    if (this._receivedIds.size > this._config.receivedIdLimit) {
      const oldestId = this._receivedIds.values().next().value;
      if (oldestId !== undefined) this._receivedIds.delete(oldestId);
    }
    return true;
  }
}
