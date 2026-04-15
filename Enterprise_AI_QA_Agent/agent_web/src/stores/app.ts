import { defineStore } from "pinia";

import { api } from "../services/api";
import type { HealthResponse } from "../types";

export const useAppStore = defineStore("app", {
  state: () => ({
    health: null as HealthResponse | null,
    loading: false,
    error: "",
  }),
  actions: {
    async fetchHealth() {
      this.loading = true;
      this.error = "";
      try {
        this.health = await api.getHealth();
      } catch (error) {
        this.error = error instanceof Error ? error.message : "服务连接失败";
      } finally {
        this.loading = false;
      }
    },
  },
});

