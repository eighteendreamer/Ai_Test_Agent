import { createApp, defineComponent, h, watch } from "vue";
import { createPinia } from "pinia";

import ChatTimeline from "../src/components/chat/ChatTimeline.vue";
import { api } from "../src/services/api";
import { useSessionStore } from "../src/stores/session";

const SESSION_ID = new URLSearchParams(window.location.search).get("session")
  || "assistant-stream-browser-probe";
const ASSISTANT_MESSAGE_ID = "assistant-stream-browser-answer";
const TOOL_KEY = "knowledge-rag";
const EXPECTED_ANSWER = [
  "这是本地确定性模型桩生成的回答：\n\n",
  "1. 后端逐片发送 AI 文本。\n",
  "2. SSE 实时送达浏览器。\n\n",
  "最终正文从持久化 transcript 对账。",
].join("");
function requiredElement(selector: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(selector);
  if (!element) throw new Error(`Assistant stream probe element is missing: ${selector}`);
  return element;
}
const statusElement = requiredElement("#probe-status");
const timelineElement = requiredElement("#timeline");
const pinia = createPinia();
const store = useSessionStore(pinia);

let completionCheckStarted = false;

createApp(defineComponent({
  setup: () => () => h(ChatTimeline, {
    messages: store.messages,
    activity: store.activity,
  }),
})).use(pinia).mount(timelineElement);

watch(
  () => ({
    status: store.session?.status || "",
    message: store.messages.find((item) => item.id === ASSISTANT_MESSAGE_ID),
  }),
  ({ status, message }) => {
    if (status === "completed" && message) {
      if (completionCheckStarted) {
        return;
      }
      completionCheckStarted = true;
      const assistantMessages = store.messages.filter(
        (item) => item.role === "assistant" && item.id === ASSISTANT_MESSAGE_ID,
      );
      if (assistantMessages.length !== 1 || message.content !== EXPECTED_ANSWER) {
        statusElement.dataset.result = "failed";
        statusElement.textContent = "失败：最终 transcript 与预期分片正文不一致。";
      } else {
        void verifyCompletedTurn();
      }
    } else if (message?.content) {
      statusElement.dataset.result = "streaming";
      statusElement.textContent = `正在接收 AI 分片：${message.content}`;
    }
  },
  { immediate: true, deep: true },
);

async function verifyCompletedTurn() {
  try {
    const health = await waitForProbeCompletion();
    const streamingEvidence = await waitForBrowserEvents();
    const backendVerified = health.status === "completed"
      && health.model_invocation_count === "2"
      && health.model_received_completed_tool_result === "true"
      && health.tool_invocation_count === "1"
      && health.tool_result_status === "completed"
      && health.tool_job_status === "completed";

    if (!backendVerified || !streamingEvidence) {
      throw new Error(
        `端到端检查失败：后端=${JSON.stringify(health)}，浏览器事件=${streamingEvidence}`,
      );
    }

    statusElement.dataset.result = "passed";
    statusElement.textContent = "通过：模型工具调用与执行成功，SSE 分片和最终 transcript 在浏览器中完全一致。";
  } catch (error) {
    statusElement.dataset.result = "failed";
    statusElement.textContent = error instanceof Error ? error.message : String(error);
  }
}

async function waitForProbeCompletion() {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const response = await fetch("/api/v1/__test__/health", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`读取探针执行状态失败，HTTP ${response.status}`);
    }
    const health = await response.json() as Record<string, string>;
    if (health.status === "completed") {
      return health;
    }
    if (health.status === "failed") {
      throw new Error(`后端模型/工具探针失败：${health.error_type} ${health.error}`);
    }
    await delay(80);
  }
  throw new Error("等待后端模型和工具执行完成超时。");
}

async function waitForBrowserEvents() {
  const deadline = Date.now() + 3_000;
  while (Date.now() < deadline) {
    const toolCompleted = store.activity.some(
      (event) => event.type === "tool.execution_completed"
        && event.payload?.tool_key === TOOL_KEY,
    );
    const receivedDeltas = store.activity
      .filter((event) => event.type === "assistant.stream.delta"
        && event.payload?.message_id === ASSISTANT_MESSAGE_ID)
      .reverse()
      .map((event) => String(event.payload?.delta || ""))
      .join("");
    if (toolCompleted && receivedDeltas === EXPECTED_ANSWER) {
      return true;
    }
    await delay(50);
  }
  return false;
}

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function runProbe() {
  try {
    store.applySession(await api.getSession(SESSION_ID));
    store.connectEvents();
    statusElement.textContent = "已加载会话；正在建立 SSE 连接…";
    window.setTimeout(async () => {
      try {
        const response = await fetch("/api/v1/__test__/assistant-stream/start", {
          method: "POST",
        });
        if (!response.ok) {
          throw new Error(`测试流启动失败，HTTP ${response.status}`);
        }
      } catch (error) {
        statusElement.dataset.result = "failed";
        statusElement.textContent = error instanceof Error ? error.message : String(error);
      }
    }, 350);
  } catch (error) {
    statusElement.dataset.result = "failed";
    statusElement.textContent = error instanceof Error ? error.message : String(error);
  }
}

void runProbe();
