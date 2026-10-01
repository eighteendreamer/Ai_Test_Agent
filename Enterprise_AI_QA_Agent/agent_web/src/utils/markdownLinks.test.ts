import { describe, expect, it } from "vitest";

import { renderAssistantMarkdown } from "./markdown";

describe("assistant markdown link protocols", () => {
  it("does not turn model-supplied desktop protocols into clickable links", () => {
    const html = renderAssistantMarkdown(
      "[settings](ms-settings:privacy) [upper](MS-SETTINGS:privacy) [mail](mailto:user@example.org) [web](https://example.org/docs)",
    );

    expect(html).not.toContain('href="ms-settings:privacy"');
    expect(html).not.toContain('href="MS-SETTINGS:privacy"');
    expect(html).not.toContain('href="mailto:user@example.org"');
    expect(html).toContain('href="https://example.org/docs"');
    expect(html).toContain('target="_blank"');
  });

  it("keeps relative artifact downloads available", () => {
    const html = renderAssistantMarkdown(
      "/api/v1/sessions/session-1/artifacts/artifact-1/content",
    );

    expect(html).toContain('href="/api/v1/sessions/session-1/artifacts/artifact-1/content"');
    expect(html).toContain('artifact-download-link');
  });

  it("opens protocol-relative web links outside the desktop window", () => {
    const html = renderAssistantMarkdown("[web](//example.org/docs)");
    expect(html).toContain('href="//example.org/docs"');
    expect(html).toContain('target="_blank"');
  });
});
