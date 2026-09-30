import { createServer as createHttpServer } from "node:http";
import { createServer as createViteServer } from "vite";
import { describe, expect, it } from "vitest";
import { createApiProxyOptions } from "../../electron/api-auth-proxy.mjs";

describe("Vite API authentication proxy", () => {
  it("forwards the configured Bearer token to the API without returning it to the browser", async () => {
    const apiToken = "vite-integration-test-token-xxxxxxxxxxxxxxxxxxxxxxxx";
    const upstream = createHttpServer((request, response) => {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ authorized: request.headers.authorization === `Bearer ${apiToken}` }));
    });
    await new Promise((resolve, reject) => {
      upstream.once("error", reject);
      upstream.listen(0, "127.0.0.1", resolve);
    });

    const upstreamAddress = upstream.address();
    if (!upstreamAddress || typeof upstreamAddress === "string") {
      throw new Error("Mock API did not bind to a TCP port.");
    }

    const vite = await createViteServer({
      configFile: false,
      logLevel: "silent",
      server: {
        host: "127.0.0.1",
        port: 0,
        strictPort: false,
        proxy: {
          "/api": createApiProxyOptions(
            `http://127.0.0.1:${upstreamAddress.port}`,
            apiToken,
          ),
        },
      },
    });

    try {
      await vite.listen();
      const viteAddress = vite.httpServer?.address();
      if (!viteAddress || typeof viteAddress === "string") {
        throw new Error("Vite proxy did not bind to a TCP port.");
      }

      const response = await fetch(`http://127.0.0.1:${viteAddress.port}/api/proxy-check`, {
        headers: { Authorization: "Bearer renderer-supplied-value" },
      });
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({ authorized: true });
    } finally {
      await vite.close();
      await new Promise((resolve, reject) => {
        upstream.close((error) => (error ? reject(error) : resolve()));
      });
    }
  }, 15000);
});
