import { afterEach, describe, expect, it, vi } from "vitest";

import { startChannelPairing } from "./settings.api";

describe("channel pairing API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls the backend pairing start route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ session_id: "pairing-1" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await startChannelPairing("feishu", { config_name: "team-channel" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/settings/channels/feishu/pairing/start",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ config_name: "team-channel" }),
      }),
    );
  });
});
