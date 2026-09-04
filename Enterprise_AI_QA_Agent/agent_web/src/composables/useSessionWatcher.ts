export interface WatcherConfig {
  pollIntervalMs: number;
  maxFailures: number;
}

const DEFAULT_CONFIG: WatcherConfig = {
  pollIntervalMs: 5000,
  maxFailures: 10,
};

export class SessionWatcherManager {
  private _timer: number | null = null;
  private _failures = 0;
  private _error = "";
  private _lastSyncAt = "";
  private _inFlight = false;
  private _config: WatcherConfig;
  private _refreshFn: (() => Promise<void>) | null = null;

  constructor(config: Partial<WatcherConfig> = {}) {
    this._config = { ...DEFAULT_CONFIG, ...config };
  }

  get timer(): number | null {
    return this._timer;
  }

  set timer(value: number | null) {
    this._timer = value;
  }

  get failures(): number {
    return this._failures;
  }

  get error(): string {
    return this._error;
  }

  get lastSyncAt(): string {
    return this._lastSyncAt;
  }

  get inFlight(): boolean {
    return this._inFlight;
  }

  set inFlight(value: boolean) {
    this._inFlight = value;
  }

  bindRefresh(refreshFn: () => Promise<void>): void {
    this._refreshFn = refreshFn;
  }

  start(): void {
    this.stop();
    this._timer = window.setInterval(() => {
      void this._tick();
    }, this._config.pollIntervalMs);
  }

  stop(): void {
    if (this._timer !== null) {
      window.clearInterval(this._timer);
      this._timer = null;
    }
  }

  recordSuccess(): void {
    this._failures = 0;
    this._error = "";
    this._lastSyncAt = new Date().toISOString();
  }

  recordFailure(error: string): void {
    this._failures += 1;
    this._error = error;
  }

  private async _tick(): Promise<void> {
    if (this._inFlight) return;
    if (!this._refreshFn) return;
    this._inFlight = true;
    try {
      await this._refreshFn();
    } catch (error) {
      this.recordFailure(error instanceof Error ? error.message : "刷新会话失败。");
    } finally {
      this._inFlight = false;
    }
  }
}
