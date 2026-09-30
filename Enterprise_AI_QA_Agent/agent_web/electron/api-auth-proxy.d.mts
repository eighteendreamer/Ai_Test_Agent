export interface ApiProxyRequest {
  setHeader(name: string, value: string): void;
}

export interface ApiProxyServer {
  on(event: "proxyReq", listener: (request: ApiProxyRequest) => void): unknown;
}

export function bearerAuthorizationValue(apiToken: unknown): string;
export function applyApiAuthorization(headers: unknown, apiToken: unknown): Record<string, string>;
export function attachApiAuthorization(proxy: ApiProxyServer, apiToken: unknown): void;
export function createApiProxyOptions(
  target: string,
  apiToken: unknown,
): {
  target: string;
  changeOrigin: true;
  configure(proxy: ApiProxyServer): void;
};
