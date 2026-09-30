export function bearerAuthorizationValue(apiToken) {
  const token = typeof apiToken === "string" ? apiToken.trim() : "";
  return token ? `Bearer ${token}` : "";
}

export function applyApiAuthorization(headers, apiToken) {
  const result = {};
  if (Array.isArray(headers)) {
    for (const [name, value] of headers) {
      result[String(name)] = String(value);
    }
  } else if (headers && typeof headers.forEach === "function") {
    headers.forEach((value, name) => {
      result[String(name)] = String(value);
    });
  } else if (headers && typeof headers === "object") {
    Object.assign(result, headers);
  }

  const authorization = bearerAuthorizationValue(apiToken);
  if (!authorization) {
    return result;
  }

  for (const name of Object.keys(result)) {
    if (name.toLowerCase() === "authorization") {
      delete result[name];
    }
  }
  result.authorization = authorization;
  return result;
}

export function attachApiAuthorization(proxy, apiToken) {
  const authorization = bearerAuthorizationValue(apiToken);
  if (!authorization) {
    return;
  }

  proxy.on("proxyReq", (proxyRequest) => {
    proxyRequest.setHeader("authorization", authorization);
  });
}

export function createApiProxyOptions(target, apiToken) {
  return {
    target,
    changeOrigin: true,
    configure(proxy) {
      attachApiAuthorization(proxy, apiToken);
    },
  };
}
