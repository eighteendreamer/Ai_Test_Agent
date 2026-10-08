// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, expect, it } from "vitest";
import { registerLocale, setLocale } from "../src/services/i18n";
import zhCN from "../src/locales/zh-CN";
import { useSessionStore } from "../src/stores/session";
import type { SessionDetail, SessionSnapshot } from "../src/types";
import SnapshotTracePanel from "../src/components/chat/SnapshotTracePanel.vue";

beforeEach(() => {
  setActivePinia(createPinia());
  registerLocale("zh-CN", zhCN);
  setLocale("zh-CN");
});

function render(snapshot: SessionSnapshot) {
  useSessionStore().session = { id: snapshot.session_id, status: "idle", metadata: {},
    last_snapshot: snapshot } as unknown as SessionDetail;
  return mount(SnapshotTracePanel);
}

it("renders the actual production PG cumulative budget after approval and restart", () => {
  const records = JSON.parse(readFileSync(resolve(process.cwd(),
    "../docs/upgrade_2026-10-03/p0-approval-budget-production-verified/3/p2-execution-worker-cli-probe/postgres-execution.json"),
    "utf8")).postgres_snapshot_table as SessionSnapshot[];
  const snapshot = records.find(row => row.stage === "completed")!;
  const wrapper = render(snapshot);
  const row = wrapper.findAll(".snapshot-trace-grid > div").find(item => item.find("dt").text() === "模型调用");
  expect(row?.find("dd").text()).toBe("3/3");
  expect(wrapper.text()).toContain("max_iterations");
  expect(snapshot.graph_state.loop_iteration).toBe(0);
  wrapper.unmount();
});

it("keeps an actual historical PG snapshot readable without inventing model counts", () => {
  const data = JSON.parse(readFileSync(resolve(process.cwd(),
    "../docs/upgrade_2026-10-03/p0-tool-evidence-production-final/trusted-parent/postgres-execution.json"), "utf8"));
  const records = data.tables.postgres_snapshot_table.records as SessionSnapshot[];
  const wrapper = render(records[records.length - 1]);
  expect(wrapper.text()).not.toContain("模型调用");
  expect(wrapper.text()).toContain("0/8");
  wrapper.unmount();
});
