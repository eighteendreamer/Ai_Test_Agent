import { createApp, defineComponent, h, nextTick } from "vue";
import { createPinia } from "pinia";
import ChatTimeline from "../src/components/chat/ChatTimeline.vue";
import { api } from "../src/services/api";
import { useSessionStore } from "../src/stores/session";
import type { ChatMessage, ExecutionEvent, SessionDetail } from "../src/types";

function percentile(samples: number[], fraction: number): number {
  const sorted = [...samples].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)] || 0;
}

// Browser CPU benchmark, not a model/provider or network throughput benchmark.
// Feed the same seeded events through the formal SSE callback, Store and Timeline.
export async function benchmarkAssistantStream(counts = [100, 500, 1000]) {
  const results = [];
  for (const count of counts) {
    const pinia = createPinia();
    const store = useSessionStore(pinia);
    const sessionId = `stream-benchmark-${count}`;
    const history: ChatMessage[] = Array.from({ length: 100 }, (_, index) => ({
      id: `history-${index}`, role: "assistant", content: `已完成历史 ${index}。`,
      created_at: "2026-10-01T00:00:00Z", metadata: { response_mode: "ok" },
    }));
    const session: SessionDetail = {
      id: sessionId, title: "Seeded stream benchmark", status: "running", messages: history,
      session_mode: "normal", runtime_mode: "interactive", mode_key: "default",
      created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
      event_count: 0, snapshot_count: 0, pending_approvals: [], control_state: "idle",
      is_resumable: false, is_interrupted: false, replay_available: false,
      verification_results: [], metadata: {},
    };
    store.applySession(session);
    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = createApp(defineComponent({ setup: () => () => h(ChatTimeline, { messages: store.messages, activity: store.activity }) }));
    app.use(pinia).mount(root);
    const originalConnect = api.connectEvents;
    let receive: (event: ExecutionEvent) => void = () => {};
    api.connectEvents = (_id, onEvent) => {
      receive = onEvent;
      return { readyState: 1, close() {}, onopen: null, onerror: null } as unknown as EventSource;
    };
    try {
      store.connectEvents(true);
      const event = (id: string, type: string, payload: Record<string, unknown>): ExecutionEvent => ({
        id, type, session_id: sessionId, turn_id: "benchmark-turn",
        timestamp: "2026-10-01T00:00:00Z", payload: { message_id: "benchmark-answer", ...payload },
      });
      receive(event("start", "assistant.stream.started", {}));
      await nextTick();
      const chunks = Array.from({ length: count }, (_, index) =>
        `条目 ${index}：验证连续分片的显示和最终正文。\n${index % 50 === 0 ? "\n$x^2$\n\n```js\nconst value = 1;\n```\n\n" : ""}`,
      );
      const samples: number[] = [];
      let firstTextMs = 0;
      const start = performance.now();
      for (const [index, delta] of chunks.entries()) {
        const before = performance.now();
        receive(event(`delta-${index}`, "assistant.stream.delta", { delta }));
        await nextTick();
        samples.push(performance.now() - before);
        if (index === 0) {
          firstTextMs = performance.now() - start;
          if (!root.textContent?.includes("条目 0")) {
            throw new Error(`First delta was not displayed immediately: stored=${store.messagesById.get("benchmark-answer")?.content}, dom=${root.textContent?.slice(-200)}`);
          }
        }
      }
      receive(event("complete", "assistant.stream.completed", {}));
      await nextTick();
      const elapsed = performance.now() - start;
      const expected = chunks.join("");
      const answer = store.messages.find((message) => message.id === "benchmark-answer");
      if (answer?.content !== expected || !root.textContent?.includes(`条目 ${count - 1}`)) {
        throw new Error(`Stream benchmark lost text at ${count} deltas.`);
      }
      results.push({
        deltas: count, characters: expected.length, elapsed_ms: elapsed,
        first_text_dom_ms: firstTextMs, characters_per_second: expected.length / (elapsed / 1000),
        delta_update_p50_ms: percentile(samples, 0.5), delta_update_p95_ms: percentile(samples, 0.95),
        delta_update_p99_ms: percentile(samples, 0.99), errors: 0,
      });
    } finally {
      store.disconnectEvents();
      store.stopWatcher();
      api.connectEvents = originalConnect;
      app.unmount();
      root.remove();
    }
  }
  return { user_agent: navigator.userAgent, history_messages: 100, results };
}
