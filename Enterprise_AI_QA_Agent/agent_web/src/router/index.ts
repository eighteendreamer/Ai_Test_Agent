import { createRouter, createWebHistory } from "vue-router";

import WorkbenchView from "../views/WorkbenchView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/home" },
    { path: "/dashboard", redirect: "/home" },
    { path: "/home", name: "home", component: WorkbenchView, meta: { label: "Home" } },
    { path: "/taskpool", name: "taskpool", component: () => import("../views/TaskPoolView.vue"), meta: { label: "Task Pool" } },
    { path: "/projects", name: "projects", component: () => import("../views/ProjectsView.vue"), meta: { label: "Test Projects" } },
    { path: "/knowledge", name: "knowledge", component: () => import("../views/KnowledgeView.vue"), meta: { label: "Knowledge" } },
    { path: "/tools", name: "tools", component: () => import("../views/ToolsView.vue"), meta: { label: "Tools" } },
    { path: "/reports", name: "reports", component: () => import("../views/ReportsView.vue"), meta: { label: "Reports" } },
    { path: "/settings", name: "settings", component: () => import("../views/SettingsView.vue"), meta: { label: "Settings" } },
  ],
});

export default router;
