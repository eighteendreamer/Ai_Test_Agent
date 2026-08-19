const FLOW_CHANNEL = "qa-agent-flow";
const FLOW_WINDOW_NAME = "qa-agent-flow";

export interface FlowWindowTarget {
  sessionId?: string;
  turnId?: string;
}

export interface FlowSessionMessage {
  type: "session-changed";
  sessionId: string;
  turnId: string;
}

function trimId(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function buildFlowPath(target: FlowWindowTarget = {}): string {
  const params = new URLSearchParams();
  const sessionId = trimId(target.sessionId);
  const turnId = trimId(target.turnId);
  if (sessionId) {
    params.set("session", sessionId);
  }
  if (turnId) {
    params.set("turn", turnId);
  }
  const query = params.toString();
  return query ? `/flow?${query}` : "/flow";
}

export async function openAgentFlowWindow(target: FlowWindowTarget = {}): Promise<void> {
  if (window.qaAgentDesktop?.isDesktop && window.qaAgentDesktop.openFlowWindow) {
    try {
      await window.qaAgentDesktop.openFlowWindow({
        sessionId: trimId(target.sessionId) || undefined,
        turnId: trimId(target.turnId) || undefined,
      });
      return;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn("[flow] desktop:open-flow-window failed, falling back to window.open:", message);
    }
  }

  const opened = window.open(buildFlowPath(target), FLOW_WINDOW_NAME);
  opened?.focus();
}

export function publishFlowSession(target: FlowWindowTarget = {}): void {
  if (typeof BroadcastChannel === "undefined") {
    return;
  }
  const channel = new BroadcastChannel(FLOW_CHANNEL);
  const message: FlowSessionMessage = {
    type: "session-changed",
    sessionId: trimId(target.sessionId),
    turnId: trimId(target.turnId),
  };
  channel.postMessage(message);
  channel.close();
}

export function subscribeFlowSession(
  handler: (target: Required<FlowWindowTarget>) => void,
): () => void {
  if (typeof BroadcastChannel === "undefined") {
    return () => {};
  }

  const channel = new BroadcastChannel(FLOW_CHANNEL);
  channel.onmessage = (event: MessageEvent<FlowSessionMessage>) => {
    if (event.data?.type !== "session-changed") {
      return;
    }
    handler({
      sessionId: trimId(event.data.sessionId),
      turnId: trimId(event.data.turnId),
    });
  };
  return () => channel.close();
}
