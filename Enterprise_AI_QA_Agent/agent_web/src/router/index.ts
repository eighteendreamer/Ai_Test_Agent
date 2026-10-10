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
    { path: "/projects/:projectId", name: "project-detail", component: () => import("../views/ProjectsView.vue"), meta: { label: "Project Detail" } },
    { path: "/projects/:projectId/recording-scenes", name: "recording-scenes", component: () => import("../views/RecordingScenesView.vue"), meta: { label: "Recording Scenes" } },
    { path: "/projects/:projectId/documents/:documentId/differences/:taskId", name: "document-difference", component: () => import("../views/DocumentDifferenceView.vue"), meta: { label: "Document Difference" } },
    { path: "/projects/:projectId/documents/:documentId/impacts/:taskId", name: "document-impact", component: () => import("../views/DocumentImpactView.vue"), meta: { label: "Document Impact" } },
    { path: "/projects/:projectId/documents/:documentId/generations/:groupId/tasks/:taskId", name: "case-generation-task", component: () => import("../views/CaseGenerationTaskView.vue"), meta: { label: "Case Generation Task" } },
    { path: "/knowledge", name: "knowledge", component: () => import("../views/KnowledgeView.vue"), meta: { label: "Knowledge" } },
    { path: "/tools", name: "tools", component: () => import("../views/ToolsView.vue"), meta: { label: "Tools" } },
    { path: "/reports", name: "reports", component: () => import("../views/ReportsView.vue"), meta: { label: "Reports" } },
    { path: "/settings", name: "settings", component: () => import("../views/SettingsView.vue"), meta: { label: "Settings" } },
    { path: "/flow", name: "flow", component: () => import("../views/FlowView.vue"), meta: { label: "Orchestration Trace", blankShell: true } },
    { path: "/recorder-window", name: "recorder-window", component: () => import("../views/RecorderWindowView.vue"), meta: { label: "UI Recorder", blankShell: true } },
  ],
});

export default router;
