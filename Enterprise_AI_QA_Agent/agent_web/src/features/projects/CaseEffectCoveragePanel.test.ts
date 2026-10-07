// @vitest-environment jsdom
import samples from "../../../tests/fixtures/case-effect-coverage.actual.json";
import relations from "../../../tests/fixtures/case-effect-relations.actual.json";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import CaseEffectCoveragePanel from "./CaseEffectCoveragePanel.vue";

function actual(scenario: keyof typeof samples) {
  const saved = samples[scenario];
  expect(saved.effect_coverage_result_connected).toBe(true);
  expect(saved.original_bytes_verified_in_RustFS).toBe(true);
  return saved.actual_formal_result.actual.effect_cleanup_coverage;
}

describe("actual original object coverage", () => {
  it("shows the real unreferenced object rather than the final output only", () => {
    const coverage = actual("one-of-two");
    const wrapper = mount(CaseEffectCoveragePanel, { props: { coverage, resultLabel: "实际失败结果" } });
    expect(wrapper.text()).toContain("已知对象 2 · 已覆盖 1");
    expect(wrapper.text()).toContain("覆盖范围未确认");
    expect(wrapper.text()).toContain(samples["one-of-two"].actual_formal_result.actual.effect_cleanup_coverage.cleanups.cleanup.uncovered[0].value);
    expect(wrapper.text()).toContain("共享消费完成尚未验证");
    wrapper.unmount();
  });
  it("renders failed GET evidence and budget gaps without a false zero", () => {
    const failed = mount(CaseEffectCoveragePanel, { props: { coverage: actual("cleanup-read"), resultLabel: "查询" } });
    expect(failed.text()).toContain("清理证据未满足"); failed.unmount();
    const budget = mount(CaseEffectCoveragePanel, { props: { coverage: actual("coverage-budget"), resultLabel: "超限" } });
    expect(budget.text()).toContain("不能按零对象处理");
    expect(budget.text()).toContain("已知对象 1 · 已覆盖 未取得"); budget.unmount();
  });
  it("preserves old result rendering by omitting absent or unsupported coverage", () => {
    for (const coverage of [undefined, null, [], {}, { version: "old" }]) {
      const wrapper = mount(CaseEffectCoveragePanel, { props: { coverage, resultLabel: "历史" } });
      expect(wrapper.find("section").exists()).toBe(false); wrapper.unmount();
    }
  });
  it("shows actual formal v2 coverage and unknown origins", () => {
    for (const [scenario, saved] of Object.entries(relations)) {
      expect(saved.RustFS_original_verified).toBe(true);
      const wrapper = mount(CaseEffectCoveragePanel, { props: { coverage: saved.coverage, resultLabel: scenario } });
      expect(wrapper.find("section").exists()).toBe(true);
      expect(wrapper.text()).toContain("已知对象 2");
      if (scenario === "internal-partial") {
        expect(wrapper.text()).toContain("已覆盖 1");
        expect(wrapper.text()).toContain(relations["internal-partial"].coverage.cleanups.target.uncovered[0].value);
      }
      if (scenario.startsWith("unknown-")) expect(wrapper.text()).toContain("unknown");
      if (scenario === "coverage-budget") expect(wrapper.text()).toContain("不能按零对象处理");
      wrapper.unmount();
    }
  });

});
