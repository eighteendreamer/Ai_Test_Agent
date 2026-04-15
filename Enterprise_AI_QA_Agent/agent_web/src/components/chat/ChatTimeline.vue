<script setup lang="ts">
import type { ChatMessage } from "../../types";

defineProps<{
  messages: ChatMessage[];
}>();

function labelForRole(role: ChatMessage["role"]) {
  if (role === "user") return "User Prompt";
  if (role === "assistant") return "Agent Response";
  if (role === "tool") return "Tool Output";
  if (role === "system") return "System";
  return "Event";
}

function toolSummary(content: string) {
  try {
    const parsed = JSON.parse(content) as { summary?: string; status?: string };
    const summary = String(parsed.summary || "").trim();
    const status = String(parsed.status || "").trim();
    if (summary && status) return `${status}: ${summary}`;
    if (summary) return summary;
    if (status) return `status: ${status}`;
  } catch {
    return content.split("\n")[0]?.trim() || "Expand to view tool output";
  }
  return "Expand to view tool output";
}

function displayAssistantContent(content: string) {
  const marker = content.indexOf("[Framework]");
  return marker >= 0 ? content.slice(0, marker).trim() : content.trim();
}

function renderAssistantMarkdown(content: string) {
  const source = displayAssistantContent(content);
  const normalized = source.replace(/\r\n/g, "\n");
  const codeBlocks: string[] = [];
  const withPlaceholders = normalized.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_, language = "", body = "") => {
    const token = `__CODE_BLOCK_${codeBlocks.length}__`;
    const escapedBody = escapeHtml(String(body).trimEnd());
    const className = language ? ` class="language-${escapeHtml(String(language))}"` : "";
    codeBlocks.push(`<pre class="assistant-code-block"><code${className}>${escapedBody}</code></pre>`);
    return token;
  });

  const lines = withPlaceholders.split("\n");
  const blocks: string[] = [];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let listKind: "ul" | "ol" | null = null;

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

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }

    if (line.startsWith("__CODE_BLOCK_") && line.endsWith("__")) {
      flushParagraph();
      flushList();
      blocks.push(line);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const ordered = line.match(/^\d+\.\s+(.*)$/);
    if (ordered) {
      flushParagraph();
      if (listKind && listKind !== "ol") flushList();
      listKind = "ol";
      listItems.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
      continue;
    }

    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      if (listKind && listKind !== "ul") flushList();
      listKind = "ul";
      listItems.push(`<li>${renderInlineMarkdown(bullet[1])}</li>`);
      continue;
    }

    if (listKind) flushList();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();

  return blocks
    .join("")
    .replace(/__CODE_BLOCK_(\d+)__/g, (_, index) => codeBlocks[Number(index)] || "");
}

function renderInlineMarkdown(content: string) {
  const inlineCodes: string[] = [];
  let html = escapeHtml(content).replace(/`([^`]+)`/g, (_, code) => {
    const token = `__INLINE_CODE_${inlineCodes.length}__`;
    inlineCodes.push(`<code>${escapeHtml(String(code))}</code>`);
    return token;
  });

  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  html = html.replace(/__(.+?)__/g, "<strong>$1</strong>");
  html = html.replace(/_(.+?)_/g, "<em>$1</em>");

  return html.replace(/__INLINE_CODE_(\d+)__/g, (_, index) => inlineCodes[Number(index)] || "");
}

function escapeHtml(content: string) {
  return content
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
</script>

<template>
  <div class="home-history" v-if="messages.length">
    <article
      v-for="message in messages"
      :key="message.id"
      class="conversation-entry"
      :class="`conversation-entry-${message.role}`"
    >
      <div class="conversation-entry-meta">
        <span>{{ labelForRole(message.role) }}</span>
        <span>{{ new Date(message.created_at).toLocaleString("zh-CN") }}</span>
      </div>
      <details v-if="message.role === 'tool'" class="tool-output-details">
        <summary class="tool-output-summary">
          <span>{{ toolSummary(message.content) }}</span>
          <span class="tool-output-hint">
            <span class="tool-output-hint-collapsed">展开</span>
            <span class="tool-output-hint-expanded">收起</span>
          </span>
        </summary>
        <pre class="conversation-entry-content tool-output-content">{{ message.content }}</pre>
      </details>
      <div
        v-else-if="message.role === 'assistant'"
        class="conversation-entry-content conversation-entry-markdown"
        v-html="renderAssistantMarkdown(message.content)"
      />
      <pre v-else class="conversation-entry-content">{{ message.content }}</pre>
    </article>
  </div>
</template>
