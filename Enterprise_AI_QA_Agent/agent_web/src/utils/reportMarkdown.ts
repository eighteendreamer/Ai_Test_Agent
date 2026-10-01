import MarkdownIt from "markdown-it";

const inlineMarkdown = new MarkdownIt({ html: false, linkify: false });
const defaultValidateLink = inlineMarkdown.validateLink.bind(inlineMarkdown);
inlineMarkdown.validateLink = (url: string) =>
  defaultValidateLink(url) && /^https?:\/\//i.test(url);
const defaultLinkRender =
  inlineMarkdown.renderer.rules.link_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));
inlineMarkdown.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noreferrer");
  return defaultLinkRender(tokens, idx, options, env, self);
};

export function renderMarkdown(content: string) {
  const normalized = content.replace(/\r\n/g, "\n");
  const rendered: string[] = [];
  let consumed = 0;
  for (const match of normalized.matchAll(/```([\w-]*)\n?([\s\S]*?)```/g)) {
    const start = match.index ?? 0;
    rendered.push(renderTextBlocks(normalized.slice(consumed, start)));
    const language = match[1];
    const escapedBody = escapeHtml(match[2].trimEnd());
    const className = language ? ` class="language-${escapeHtml(language)}"` : "";
    rendered.push(`<pre class="assistant-code-block"><code${className}>${escapedBody}</code></pre>`);
    consumed = start + match[0].length;
  }
  rendered.push(renderTextBlocks(normalized.slice(consumed)));
  return rendered.join("");
}

function renderTextBlocks(content: string) {
  const lines = content.split("\n");
  const blocks: string[] = [];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let listKind: "ul" | "ol" | null = null;
  let tableRows: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };

  const flushList = () => {
    if (!listItems.length || !listKind) return;
    blocks.push(`<${listKind}>${listItems.join("")}</${listKind}>`);
    listItems = [];
    listKind = null;
  };

  const flushTable = () => {
    if (!tableRows.length) return;
    blocks.push(renderMarkdownTable(tableRows));
    tableRows = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      flushTable();
      continue;
    }

    if (isMarkdownTableRow(line)) {
      flushParagraph();
      flushList();
      tableRows.push(line);
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
      flushParagraph();
      flushList();
      flushTable();
      blocks.push("<hr>");
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      flushList();
      flushTable();
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const ordered = line.match(/^\d+\.\s+(.*)$/);
    if (ordered) {
      flushParagraph();
      flushTable();
      if (listKind && listKind !== "ol") flushList();
      listKind = "ol";
      listItems.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
      continue;
    }

    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      flushTable();
      if (listKind && listKind !== "ul") flushList();
      listKind = "ul";
      listItems.push(`<li>${renderInlineMarkdown(bullet[1])}</li>`);
      continue;
    }

    if (listKind) flushList();
    if (tableRows.length) flushTable();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  flushTable();

  return blocks.join("");
}

function isMarkdownTableRow(line: string) {
  return line.startsWith("|") && line.endsWith("|") && line.split("|").length >= 4;
}

function isMarkdownTableSeparator(line: string) {
  const cells = splitMarkdownTableCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, "")));
}

function splitMarkdownTableCells(line: string) {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(rows: string[]) {
  if (rows.length < 2 || !isMarkdownTableSeparator(rows[1])) {
    return `<p>${rows.map((row) => renderInlineMarkdown(row)).join("<br>")}</p>`;
  }

  const headers = splitMarkdownTableCells(rows[0]);
  const bodyRows = rows.slice(2).filter((row) => !isMarkdownTableSeparator(row));
  const headHtml = headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("");
  const bodyHtml = bodyRows
    .map((row) => {
      const cells = splitMarkdownTableCells(row);
      return `<tr>${cells.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`;
    })
    .join("");

  return `<div class="assistant-table-shell"><table><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`;
}

function renderInlineMarkdown(content: string) {
  return inlineMarkdown.renderInline(content);
}

function escapeHtml(content: string) {
  return content
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
