import { defineStore } from "pinia";

import { api } from "../services/api";
import type {
  AgentDescriptor,
  HealthResponse,
  ServiceCheckItem,
  ServiceCheckStatus,
  SystemStatusSummary,
  ToolDescriptor,
} from "../types";

function buildCheck(
  key: string,
  label: string,
  status: ServiceCheckStatus,
  detail: string,
  meta?: string,
): ServiceCheckItem {
  return { key, label, status, detail, meta };
}

type ThemeMode = "light" | "dark";

const THEME_STORAGE_KEY = "enterprise-ai-qa-agent-theme";

function applyThemeToDocument(theme: ThemeMode) {
  document.documentElement.dataset.theme = theme;
  document.body.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

export const useAppStore = defineStore("app", {
  state: () => ({
    health: null as HealthResponse | null,
    agents: null as AgentDescriptor[] | null,
    tools: null as ToolDescriptor[] | null,
    loading: false,
    error: "",
    lastCheckedAt: "" as string,
    theme: "light" as ThemeMode,
  }),
  getters: {
    systemStatus(state): SystemStatusSummary {
      const checks: ServiceCheckItem[] = [];
      const backendOnline = state.health?.status === "ok";
      const memoryBackend = state.health?.memory_backend ?? "unknown";
      const knowledgeEnabled = state.health?.knowledge_enabled ?? false;
      const knowledgeTarget = state.health?.knowledge_target ?? "";

      checks.push(
        backendOnline
          ? buildCheck(
              "backend",
              "后端服务",
              "online",
              `后端服务运行正常，环境 ${state.health?.environment ?? "unknown"}`,
              state.health?.name ?? "API",
            )
          : buildCheck("backend", "后端服务", "offline", "健康检查接口未响应，后端尚未启动或代理未连通"),
      );

      checks.push(
        Array.isArray(state.agents)
          ? state.agents.length > 0
            ? buildCheck(
                "agents",
                "Agent 注册",
                "online",
                `已注册 ${state.agents.length} 个 Agent`,
                `${state.agents.length} agents`,
              )
            : buildCheck("agents", "Agent 注册", "degraded", "后端已连接，但当前没有可用 Agent")
          : buildCheck("agents", "Agent 注册", "offline", "无法读取 Agent 注册表"),
      );

      checks.push(
        Array.isArray(state.tools)
          ? state.tools.length > 0
            ? buildCheck(
                "tools",
                "工具注册",
                "online",
                `已注册 ${state.tools.length} 个工具`,
                `${state.tools.length} tools`,
              )
            : buildCheck("tools", "工具注册", "degraded", "后端已连接，但当前没有可用工具")
          : buildCheck("tools", "工具注册", "offline", "无法读取工具注册表"),
      );

      if (!backendOnline) {
        checks.push(
          buildCheck("knowledge", "知识库连接", "offline", "后端未在线，暂时无法确认知识库状态"),
        );
      } else if (!knowledgeEnabled) {
        checks.push(
          buildCheck(
            "knowledge",
            "知识库连接",
            "offline",
            "知识库已被显式关闭，当前不会建立远端连接",
            "disabled",
          ),
        );
      } else if (memoryBackend === "qdrant") {
        checks.push(
          buildCheck(
            "knowledge",
            "知识库连接",
            "online",
            "知识库向量存储已连通",
            knowledgeTarget || "Qdrant",
          ),
        );
      } else if (memoryBackend === "arangodb") {
        checks.push(
          buildCheck(
            "knowledge",
            "知识库连接",
            "online",
            "知识库文档存储已连通",
            knowledgeTarget || "ArangoDB",
          ),
        );
      } else if (memoryBackend === "arangodb_unavailable") {
        checks.push(
          buildCheck(
            "knowledge",
            "知识库连接",
            "offline",
            "ArangoDB 未连通，请检查数据库地址、账号密码和库名配置",
            knowledgeTarget || "ArangoDB",
          ),
        );
      } else if (memoryBackend === "local_memory") {
        checks.push(
          buildCheck(
            "knowledge",
            "知识库连接",
            "offline",
            "未连接到远端知识库，当前仅回退到本地内存缓存，不视为知识库已连接",
            "local_memory",
          ),
        );
      } else {
        checks.push(
          buildCheck(
            "knowledge",
            "知识库连接",
            "offline",
            "知识库后端状态未知，请检查 memory backend 配置",
            memoryBackend,
          ),
        );
      }

      const totalCount = checks.length;
      const onlineCount = checks.filter((check) => check.status === "online").length;
      const hasOffline = checks.some((check) => check.status === "offline");
      const hasDegraded = checks.some((check) => check.status === "degraded");
      const tone: ServiceCheckStatus = hasOffline ? "offline" : hasDegraded ? "degraded" : "online";
      const label =
        tone === "online" ? "系统就绪" : tone === "degraded" ? "部分未就绪" : "服务离线";

      return {
        label,
        tone,
        checks,
        onlineCount,
        totalCount,
      };
    },
  },
  actions: {
    hydrateTheme() {
      const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
      const nextTheme: ThemeMode = savedTheme === "dark" ? "dark" : "light";
      this.theme = nextTheme;
      applyThemeToDocument(nextTheme);
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      window.localStorage.setItem(THEME_STORAGE_KEY, this.theme);
      applyThemeToDocument(this.theme);
    },
    async fetchSystemStatus() {
      this.loading = true;
      this.error = "";

      const [healthResult, agentsResult, toolsResult] = await Promise.allSettled([
        api.getHealth(),
        api.listAgents(),
        api.listTools(),
      ]);

      if (healthResult.status === "fulfilled") {
        this.health = healthResult.value;
      } else {
        this.health = null;
      }

      if (agentsResult.status === "fulfilled") {
        this.agents = agentsResult.value;
      } else {
        this.agents = null;
      }

      if (toolsResult.status === "fulfilled") {
        this.tools = toolsResult.value;
      } else {
        this.tools = null;
      }

      const failedMessages = [healthResult, agentsResult, toolsResult]
        .filter((result): result is PromiseRejectedResult => result.status === "rejected")
        .map((result) => {
          const reason = result.reason;
          return reason instanceof Error ? reason.message : String(reason);
        })
        .filter(Boolean);

      this.error = failedMessages[0] ?? "";
      this.lastCheckedAt = new Date().toISOString();
      this.loading = false;
    },
  },
});
