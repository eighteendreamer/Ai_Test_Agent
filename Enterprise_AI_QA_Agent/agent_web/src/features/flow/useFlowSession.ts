import { computed, onBeforeUnmount, ref, watch, type Ref } from "vue";

import { api } from "../../services/api";
import type { ExecutionEvent, SessionSnapshot, ToolArtifactRecord, ToolJobRecord } from "../../types";
import { pickSnapshotForTurn } from "./inspect";
import { projectStageStatuses, resolveLatestTurnId } from "./stages";

const SIDE_DATA_REFRESH_TYPES = new Set([
  "graph.prompt_assembled",
  "graph.response_ready",
  "graph.execution_completed",
  "model.response_received",
  "tool.execution_completed",
  "tool.execution_failed",
  "tool.execution_denied",
  "turn.completed",
  "turn.failed",
  "runtime.turn_completed",
]);

export function useFlowSession(sessionId: Ref<string>, turnId: Ref<string>) {
  const events = ref<ExecutionEvent[]>([]);
  const snapshots = ref<SessionSnapshot[]>([]);
  const toolJobs = ref<ToolJobRecord[]>([]);
  const artifacts = ref<ToolArtifactRecord[]>([]);
  const sideDataLoaded = ref(false);
  const loading = ref(false);
  const error = ref("");
  const seenEventIds = new Set<string>();

  let eventSource: EventSource | null = null;
  let sideDataTimer: number | null = null;

  const resolvedTurnId = computed(() => turnId.value || resolveLatestTurnId(events.value));
  const statuses = computed(() => projectStageStatuses(events.value, resolvedTurnId.value));
  const activeSnapshot = computed(() => pickSnapshotForTurn(snapshots.value, resolvedTurnId.value));
  const graphState = computed(() => activeSnapshot.value?.graph_state ?? null);

  function disconnect() {
    eventSource?.close();
    eventSource = null;
    if (sideDataTimer !== null) {
      window.clearTimeout(sideDataTimer);
      sideDataTimer = null;
    }
  }

  function rememberEvent(event: ExecutionEvent): boolean {
    const eventId = String(event.id || "").trim();
    if (!eventId) {
      return true;
    }
    if (seenEventIds.has(eventId)) {
      return false;
    }
    seenEventIds.add(eventId);
    return true;
  }

  async function loadSideData(id: string) {
    try {
      const [nextSnapshots, nextJobs, nextArtifacts] = await Promise.all([
        api.listSessionSnapshots(id, { limit: 20, includeGraphState: true }),
        api.listToolJobs(id),
        api.listArtifacts(id),
      ]);
      snapshots.value = Array.isArray(nextSnapshots) ? nextSnapshots : [];
      toolJobs.value = Array.isArray(nextJobs) ? nextJobs : [];
      artifacts.value = Array.isArray(nextArtifacts) ? nextArtifacts : [];
      sideDataLoaded.value = true;
    } catch (err) {
      sideDataLoaded.value = false;
      console.warn("[flow] failed to load inspector side data", err);
    }
  }

  function scheduleSideDataRefresh(id: string) {
    if (sideDataTimer !== null) {
      window.clearTimeout(sideDataTimer);
    }
    sideDataTimer = window.setTimeout(() => {
      sideDataTimer = null;
      void loadSideData(id);
    }, 400);
  }

  function connect(id: string) {
    disconnect();
    const lastEventId = events.value.map((event) => String(event.id || "").trim()).filter(Boolean).at(-1) || "";
    eventSource = api.connectEvents(
      id,
      (event) => {
        if (!rememberEvent(event)) {
          return;
        }
        events.value = [...events.value, event];
        if (SIDE_DATA_REFRESH_TYPES.has(event.type)) {
          scheduleSideDataRefresh(id);
        }
      },
      lastEventId,
    );
    eventSource.onerror = () => {
      console.warn("[flow] SSE connection error", { sessionId: id });
    };
  }

  async function load() {
    const id = sessionId.value;
    disconnect();
    events.value = [];
    snapshots.value = [];
    toolJobs.value = [];
    artifacts.value = [];
    sideDataLoaded.value = false;
    seenEventIds.clear();
    error.value = "";

    if (!id) {
      loading.value = false;
      return;
    }

    loading.value = true;
    try {
      const history = await api.listSessionEvents(id);
      events.value = Array.isArray(history) ? history : [];
      for (const event of events.value) {
        rememberEvent(event);
      }
      await loadSideData(id);
      connect(id);
    } catch (err) {
      error.value = err instanceof Error ? err.message : "加载事件失败。";
    } finally {
      loading.value = false;
    }
  }

  watch(
    sessionId,
    () => {
      void load();
    },
    { immediate: true },
  );

  onBeforeUnmount(() => {
    disconnect();
  });

  return {
    events,
    snapshots,
    toolJobs,
    artifacts,
    sideDataLoaded,
    loading,
    error,
    resolvedTurnId,
    statuses,
    activeSnapshot,
    graphState,
    reload: load,
  };
}
