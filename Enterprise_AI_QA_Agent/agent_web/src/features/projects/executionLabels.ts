export const resourceLabels: Record<string, string> = {
  agent: "Agent", browser: "浏览器", docker: "Docker", test_account: "测试账号", environment: "环境",
};

export function waitingReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    browser_capacity: "等待浏览器", docker_capacity: "等待 Docker",
    test_account_busy: "等待测试账号", test_account_capacity: "等待测试账号",
    environment_capacity: "等待环境并发槽位", project_quota: "等待项目配额",
    global_quota: "等待全局配额", resource_busy: "资源已占用",
    resource_unavailable: "等待资源", resource_harness_unavailable: "资源调度暂不可用",
    resource_cleanup_pending: "等待资源清理",
    resource_pending_reap: "等待资源回收",
  };
  if (labels[reason]) return labels[reason];
  const [scope, , type] = reason.split(":");
  if (scope === "global") return "等待全局配额";
  if (scope === "project") return "等待项目配额";
  if (scope === "run") return "等待本次运行配额";
  if (scope === "resource_type") return `等待${resourceLabels[reason.slice(14)] || "资源"}`;
  if (scope === "project_resource_type") return `等待项目${resourceLabels[type] || "资源"}配额`;
  if (scope === "run_resource_type") return `等待本次运行${resourceLabels[type] || "资源"}配额`;
  return `等待资源（${reason}）`;
}
