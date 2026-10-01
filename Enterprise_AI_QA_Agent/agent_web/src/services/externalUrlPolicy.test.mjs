import { describe, expect, it } from "vitest";

import { externalWebUrl } from "../../electron/external-url-policy.mjs";

describe("desktop external URL policy", () => {
  it("allows canonical HTTP and HTTPS links", () => {
    expect(externalWebUrl("https://example.org/docs#part-1")).toBe("https://example.org/docs#part-1");
    expect(externalWebUrl("HTTP://example.org/path")).toBe("http://example.org/path");
  });

  it.each([
    "ms-settings:privacy",
    "mailto:user@example.org",
    "file:///C:/Windows/system.ini",
    "javascript:alert(1)",
    "data:text/html,unsafe",
    "C:\\Windows\\system.ini",
    "//example.org/path",
    "/local/path",
    "not a URL",
  ])("rejects a non-web external target: %s", (url) => {
    expect(externalWebUrl(url)).toBeNull();
  });
});
