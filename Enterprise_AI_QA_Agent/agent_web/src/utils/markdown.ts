import MarkdownIt from "markdown-it";
import hljs from "highlight.js/lib/common";
import texmath from "markdown-it-texmath";
import katex from "katex";

/**
 * Shared markdown renderer for assistant output. Mirrors the capabilities of
 * Kun's Streamdown pipeline (GFM + syntax-highlighted code + KaTeX math) but
 * built on the Vue-friendly markdown-it stack.
 */
const md: MarkdownIt = new MarkdownIt({
  html: false, // never trust raw HTML from the model
  linkify: true,
  breaks: true, // chat models rely on single newlines as soft breaks
  highlight: (code: string, language: string): string => {
    const lang = (language || "").toLowerCase();
    let inner = "";
    if (lang && hljs.getLanguage(lang)) {
      try {
        inner = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
      } catch {
        inner = escapeHtml(code);
      }
    } else {
      try {
        inner = hljs.highlightAuto(code).value;
      } catch {
        inner = escapeHtml(code);
      }
    }
    const label = lang || "text";
    // The copy button is wired up via event delegation in AssistantMarkdown.vue.
    return (
      `<div class="assistant-code-block" data-code-lang="${escapeHtml(label)}">` +
      `<div class="assistant-code-head">` +
      `<span class="assistant-code-lang">${escapeHtml(label)}</span>` +
      `<button type="button" class="assistant-code-copy" data-code-copy aria-label="Copy code">复制</button>` +
      `</div>` +
      `<pre class="hljs"><code class="hljs language-${escapeHtml(label)}">${inner}</code></pre>` +
      `</div>`
    );
  },
});

md.use(texmath, {
  engine: katex,
  delimiters: "dollars", // $inline$ and $$block$$
  katexOptions: { throwOnError: false, errorColor: "#ef4444" },
});

const defaultValidateLink = md.validateLink.bind(md);
md.validateLink = (url: string): boolean => {
  const scheme = /^([a-z][a-z\d+.-]*):/i.exec(url);
  return defaultValidateLink(url) && (!scheme || ["http", "https"].includes(scheme[1].toLowerCase()));
};

// Open links in a new tab safely.
const defaultLinkRender =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  const href = token.attrGet("href") || "";
  if (/^https?:\/\//i.test(href) || href.startsWith("//")) {
    token.attrSet("target", "_blank");
    token.attrSet("rel", "noreferrer noopener");
  }
  if (/^\/api\/v1\/sessions\/[^/]+\/artifacts\/[^/]+\/content$/i.test(href)) {
    token.attrSet("download", "");
    token.attrJoin("class", "artifact-download-link");
  }
  return defaultLinkRender(tokens, idx, options, env, self);
};

function linkBareArtifactUrls(content: string): string {
  const pattern = /(^|[\s(：:])(\/api\/v1\/sessions\/[^/\s]+\/artifacts\/[^/\s]+\/content)(?=[\s).,;:!?？。”》、]|$)/g;
  return content.replace(pattern, (_match, prefix: string, url: string) => `${prefix}[点击下载](${url})`);
}

/** Strip the internal framework marker before rendering. */
export function displayAssistantContent(content: string): string {
  const marker = content.indexOf("[Framework]");
  return marker >= 0 ? content.slice(0, marker).trim() : content.trim();
}

export function renderAssistantMarkdown(content: string): string {
  const source = linkBareArtifactUrls(displayAssistantContent(content));
  if (!source) {
    return "";
  }
  return md.render(source);
}

function escapeHtml(content: string): string {
  return content
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
