import { defineStore } from "pinia";

import { api } from "../services/api";
import type {
  AgentDescriptor,
  ChatMessage,
  ExecutionEvent,
  SessionDetail,
  ToolDescriptor,
} from "../types";

export const useSessionStore = defineStore("session", {
  state: () => ({
    session: null as SessionDetail | null,
    messages: [] as ChatMessage[],
    activity: [] as ExecutionEvent[],
    agents: [] as AgentDescriptor[],
    tools: [] as ToolDescriptor[],
    selectedAgentKey: "coordinator",
    isBootstrapping: false,
    isSending: false,
    error: "",
    eventSource: null as EventSource | null,
  }),
  getters: {
    activeAgent(state) {
      return state.agents.find((item) => item.key === state.selectedAgentKey) ?? null;
    },
  },
  actions: {
    async bootstrap() {
      if (this.isBootstrapping) {
        return;
      }

      this.isBootstrapping = true;
      this.error = "";
      try {
        const [agents, tools] = await Promise.all([
          api.listAgents(),
          api.listTools(),
        ]);
        this.agents = agents;
        this.tools = tools;
        this.selectedAgentKey = agents[0]?.key ?? "coordinator";

        if (!this.session) {
          const session = await api.createSession(
            "Enterprise Intelligent QA Session",
            this.selectedAgentKey,
          );
          this.applySession(session);
          this.connectEvents();
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Initialization failed.";
      } finally {
        this.isBootstrapping = false;
      }
    },
    applySession(session: SessionDetail) {
      this.session = session;
      this.messages = session.messages;
      this.selectedAgentKey = session.selected_agent ?? this.selectedAgentKey;
    },
    connectEvents() {
      if (!this.session) {
        return;
      }
      this.eventSource?.close();
      this.eventSource = api.connectEvents(this.session.id, (event) => {
        this.activity = [event, ...this.activity].slice(0, 50);
      });
      this.eventSource.onerror = () => {
        this.error = "Event stream disconnected. Refresh the session to reconnect.";
      };
    },
    async refreshSession() {
      if (!this.session) {
        return;
      }
      const detail = await api.getSession(this.session.id);
      this.applySession(detail);
    },
    async sendMessage(content: string) {
      if (!this.session || !content.trim()) {
        return;
      }

      this.isSending = true;
      this.error = "";
      try {
        const response = await api.sendMessage(
          this.session.id,
          content.trim(),
          this.selectedAgentKey,
        );
        this.applySession(response.session);
        this.activity = [...response.events.slice().reverse(), ...this.activity].slice(0, 50);
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Failed to send message.";
      } finally {
        this.isSending = false;
      }
    },
  },
});
