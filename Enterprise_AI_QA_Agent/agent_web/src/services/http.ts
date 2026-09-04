export async function readErrorMessage(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const payload = await response.json() as Record<string, unknown>;
      const detail = payload.detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail.trim();
      }
      const message = payload.message;
      if (typeof message === "string" && message.trim()) {
        return message.trim();
      }
      return JSON.stringify(payload);
    } catch {
      return `Request failed: ${response.status}`;
    }
  }

  try {
    const detail = await response.text();
    return detail || `Request failed: ${response.status}`;
  } catch {
    return `Request failed: ${response.status}`;
  }
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const detail = await readErrorMessage(response);
    throw new Error(detail || `Request failed: ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}
