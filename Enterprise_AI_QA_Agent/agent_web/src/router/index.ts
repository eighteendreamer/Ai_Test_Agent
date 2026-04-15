import { createRouter, createWebHistory } from "vue-router";

import KnowledgeView from "../views/KnowledgeView.vue";
import ReportsView from "../views/ReportsView.vue";
import SettingsView from "../views/SettingsView.vue";
import TaskPoolView from "../views/TaskPoolView.vue";
import ToolsView from "../views/ToolsView.vue";
import WorkbenchView from "../views/WorkbenchView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/home" },
    { path: "/dashboard", redirect: "/home" },
    { path: "/home", name: "home", component: WorkbenchView, meta: { label: "Home" } },
    { path: "/taskpool", name: "taskpool", component: TaskPoolView, meta: { label: "Task Pool" } },
    { path: "/knowledge", name: "knowledge", component: KnowledgeView, meta: { label: "Knowledge" } },
    { path: "/tools", name: "tools", component: ToolsView, meta: { label: "Tools" } },
    { path: "/reports", name: "reports", component: ReportsView, meta: { label: "Reports" } },
    { path: "/settings", name: "settings", component: SettingsView, meta: { label: "Settings" } },
  ],
});

export default router;