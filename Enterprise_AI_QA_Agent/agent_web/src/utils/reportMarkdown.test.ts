import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./reportMarkdown";

describe("report Markdown rendering", () => {
  it("keeps report text that resembles a fenced-code placeholder", () => {
    const html = renderMarkdown("__CODE_BLOCK_0__\n\n```text\nreal code\n```");

    expect(html).toContain("<p><strong>CODE_BLOCK_0</strong></p>");
    expect(html).toContain('<code class="language-text">real code</code>');
    expect(html.match(/real code/g)).toHaveLength(1);
  });

  it("keeps report text that resembles an inline-code placeholder", () => {
    const html = renderMarkdown("Marker @@IC0@@ then `value`");

    expect(html).toContain("Marker @@IC0@@ then <code>value</code>");
    expect(html.match(/<code>value<\/code>/g)).toHaveLength(1);
  });

  it("preserves ordinary report structure and escapes code", () => {
    const html = renderMarkdown("# Evidence\n\n| Item | Result |\n| --- | --- |\n| A | pass |\n\n```text\n<script>\n```");

    expect(html).toContain("<h1>Evidence</h1>");
    expect(html).toContain("<th>Item</th>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("keeps emphasis and links around inline code", () => {
    const html = renderMarkdown("**Step `run` passed** [source](https://example.test/evidence) [mail](mailto:private@example.test)");

    expect(html).toContain("<strong>Step <code>run</code> passed</strong>");
    expect(html).toContain('href="https://example.test/evidence"');
    expect(html).not.toContain('href="mailto:private@example.test"');
  });
});
