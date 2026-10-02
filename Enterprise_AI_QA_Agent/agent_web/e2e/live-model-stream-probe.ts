import { createApp, defineComponent, h, watch } from "vue";
import { createPinia } from "pinia";

import ChatTimeline from "../src/components/chat/ChatTimeline.vue";
import { api } from "../src/services/api";
import { useSessionStore } from "../src/stores/session";
import type { ExecutionEvent } from "../src/types";
import "../src/styles.css";

// Observe the production store/component. No SSE, API or reducer is replaced.
const sessionId = new URLSearchParams(window.location.search).get("session");
if (!sessionId) throw new Error("An isolated test session ID is required.");
const pinia = createPinia();
const store = useSessionStore(pinia);
const observed = new Map<string, ExecutionEvent>();
const status = document.querySelector<HTMLElement>("#probe-status")!;

createApp(defineComponent({
  setup: () => () => h(ChatTimeline, {
    messages: store.messages,
    activity: store.activity,
  }),
})).use(pinia).mount("#timeline");

watch(() => store.activity, (events) => {
  for (const event of [...events].reverse()) {
    if (!event.id) continue;
    if (!observed.has(event.id)) observed.set(event.id, event);
  }
}, { flush: "sync" });

function observations() {
  const events = [...observed.values()];
  const final = [...store.messages].reverse().find(
    (message) => message.role === "assistant" && message.content,
  );
  const deltas = events.filter((event) => event.type === "assistant.stream.delta"
    && event.payload.message_id === final?.id);
  return {
    status: store.session?.status || "",
    deltaCount: deltas.length,
    finalChars: final?.content.length || 0,
    streamMatchesFinal: Boolean(final && deltas.length
      && deltas.map((event) => String(event.payload.delta)).join("") === final.content),
    finalMessageUnique: Boolean(final && store.messages.filter((message) => message.id === final.id).length === 1),
    toolCompleted: events.some((event) => event.type === "tool.execution_completed"
      && event.payload.tool_key === "session-history"),
    turnCompleted: events.some((event) => event.type === "turn.completed"),
    error: store.error,
  };
}

declare global {
  interface Window {
    liveModelProbe: {
      observations: typeof observations;
      compareFinal: (id: string, content: string) => boolean;
    };
  }
}

window.liveModelProbe = {
  observations,
  compareFinal: (id, content) => store.messages.find((message) => message.id === id)?.content === content,
};

async function connect() {
  store.applySession(await api.getSession(sessionId!));
  store.connectEvents();
  status.dataset.ready = "true";
  status.textContent = "已连接正式会话 Store；等待测试提交。";
}

void connect().catch((error) => {
  status.dataset.ready = "failed";
  status.textContent = error instanceof Error ? error.message : String(error);
});

window.addEventListener("beforeunload", () => store.disconnectEvents());
