import { describe, expect, it } from "vitest";
import {
  applyApiAuthorization,
  attachApiAuthorization,
} from "../../electron/api-auth-proxy.mjs";

describe("server-side API proxy authentication", () => {
  it("uses the configured token instead of a renderer-supplied Authorization header", () => {
    expect(
      applyApiAuthorization(
        { Authorization: "Bearer renderer-value", accept: "application/json" },
        "proxy-secret-value",
      ),
    ).toEqual({ accept: "application/json", authorization: "Bearer proxy-secret-value" });
  });

  it("preserves client headers when no proxy token is configured", () => {
    expect(applyApiAuthorization({ authorization: "Bearer automation-token" }, " ")).toEqual({
      authorization: "Bearer automation-token",
    });
  });

  it("sets the Bearer header on Vite proxy requests without exposing it to renderer code", () => {
    let onProxyRequest: ((request: { setHeader: (name: string, value: string) => void }) => void) | undefined;
    attachApiAuthorization(
      {
        on(event: string, listener: typeof onProxyRequest) {
          expect(event).toBe("proxyReq");
          onProxyRequest = listener;
        },
      },
      "vite-proxy-secret",
    );

    const headers: Record<string, string> = {};
    onProxyRequest?.({
      setHeader: (name, value) => {
        headers[name] = value;
      },
    });
    expect(headers).toEqual({ authorization: "Bearer vite-proxy-secret" });
  });
});
