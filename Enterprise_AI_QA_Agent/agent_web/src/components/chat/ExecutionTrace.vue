<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { ExecutionEvent } from "../../types";

const props = defineProps<{
  events: ExecutionEvent[];
  turnId: string;
}>();

type TraceKind = "thinking" | "tools" | "intermediate";

type TraceGroup = {
  kind: TraceKind;
  label: string;
  summary: string;
  count: number;
  status: "active" | "complete" | "failed";
};

const open = ref(false);
const previousTurnId = ref("");

const hasAssistantOutputStarted = computed(() =>
  props.events.some((event) =>
    ["assistant.stream.started", "graph.response_ready"].includes(event.type),
  ),
);

const isFinished = computed(() =>
  hasAssistantOutputStarted.value ||
  props.events.some((event) =>
    ["turn.completed", "turn.interrupted", "graph.execution_completed"].includes(event.type),
  ),
);

const groups = computed<TraceGroup[]>(() => {
  const source = props.events;
  const toolEvents = source.filter((event) => event.type.startsWith("tool."));
  const thinkingEvents = source.filter((event) =>
    [
      "runtime.turn_started",
      "graph.execution_started",
      "graph.route_selected",
      "graph.plan_built",
      "graph.context_built",
      "graph.prompt_assembled",
      "model.request_prepared",
      "model.response_received",
    ].includes(event.type),
  );
  const intermediateEvents = source.filter((event) =>
    ["model.tool_calls_received", "graph.loop_prepared"].includes(event.type),
  );

  const latestSummary = (events: ExecutionEvent[], fallback: string) => {
    const latest = events.at(-1);
    const summary = String(latest?.payload?.summary || latest?.payload?.message || "").trim();
    return summary || fallback;
  };
  const statusFor = (events: ExecutionEvent[]): TraceGroup["status"] => {
    if (events.some((event) => ["tool.execution_failed", "tool.execution_denied"].includes(event.type))) {
      return "failed";
    }
    return isFinished.value ? "complete" : "active";
  };

  return [
    thinkingEvents.length
      ? {
          kind: "thinking",
          label: "思考过程",
          summary: latestSummary(thinkingEvents, "整理上下文并准备模型请求"),
          count: thinkingEvents.length,
          status: statusFor(thinkingEvents),
        }
      : null,
    toolEvents.length
      ? {
          kind: "tools",
          label: "工具执行",
          summary: latestSummary(toolEvents, "调用工具或加载 Skill"),
          count: toolEvents.length,
          status: statusFor(toolEvents),
        }
      : null,
    intermediateEvents.length
      ? {
          kind: "intermediate",
          label: "中间输出",
          summary: latestSummary(intermediateEvents, "整理工具结果，继续生成"),
          count: intermediateEvents.length,
          status: statusFor(intermediateEvents),
        }
      : null,
  ].filter((group): group is TraceGroup => Boolean(group));
});

const totalCount = computed(() => groups.value.reduce((sum, group) => sum + group.count, 0));
const title = computed(() => (isFinished.value ? "已完成" : "正在处理"));

watch(
  () => [props.turnId, props.events.length, isFinished.value] as const,
  ([turnId, , finished], previous) => {
    if (turnId && turnId !== previousTurnId.value) {
      previousTurnId.value = turnId;
      open.value = !finished;
      return;
    }
    if (finished) open.value = false;
  },
  { immediate: true },
);

function toggle() {
  open.value = !open.value;
}
</script>

<template>
  <section v-if="groups.length" class="chat-execution-trace" :class="{ 'is-open': open }" aria-live="polite">
    <button type="button" class="chat-execution-trace-head" :aria-expanded="open" @click="toggle">
      <span class="chat-execution-trace-chevron" aria-hidden="true">
        <i class="fa-solid fa-chevron-right"></i>
      </span>
      <span class="chat-execution-trace-label">{{ title }}</span>
      <span class="chat-execution-trace-count">{{ totalCount }} 步</span>
      <span v-if="!open" class="chat-execution-trace-summary">{{ groups.at(-1)?.summary }}</span>
    </button>
    <div v-if="open" class="chat-execution-trace-list">
      <div v-for="group in groups" :key="group.kind" class="chat-execution-step" :class="`is-${group.status}`">
        <span class="chat-execution-step-marker" aria-hidden="true">
          <i v-if="group.status === 'complete'" class="fa-solid fa-check"></i>
          <i v-else-if="group.status === 'failed'" class="fa-solid fa-xmark"></i>
          <i v-else class="fa-solid fa-spinner fa-spin"></i>
        </span>
        <div class="chat-execution-step-copy">
          <strong>{{ group.label }}</strong>
          <span>{{ group.summary }}<template v-if="group.count > 1"> · {{ group.count }} 项</template></span>
        </div>
      </div>
    </div>
  </section>
</template>
