import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionWatcherManager } from "./useSessionWatcher";

describe("session watcher failures", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("stops polling after repeated refresh failures and can be restarted explicitly", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
    const watcher = new SessionWatcherManager({ pollIntervalMs: 100, maxFailures: 2 });
    const refresh = vi.fn(async () => { watcher.recordFailure("session not found"); });
    watcher.bindRefresh(refresh);
    watcher.start();

    await vi.advanceTimersByTimeAsync(300);
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(watcher.failures).toBe(2);
    expect(watcher.timer).toBeNull();

    watcher.start();
    await vi.advanceTimersByTimeAsync(100);
    expect(refresh).toHaveBeenCalledTimes(3);
    expect(watcher.failures).toBe(1);
    watcher.stop();
  });
});
