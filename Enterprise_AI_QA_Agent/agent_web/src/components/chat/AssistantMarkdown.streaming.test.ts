// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as markdown from "../../utils/markdown";
import AssistantMarkdown from "./AssistantMarkdown.vue";

describe("assistant rendering during generation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("defers Markdown, highlighting and math until completion without losing text", async () => {
    const render = vi.spyOn(markdown, "renderAssistantMarkdown");
    const wrapper = mount(AssistantMarkdown, { props: { content: "**生成中**", streaming: true } });
    for (let index = 0; index < 50; index += 1) {
      await wrapper.setProps({ content: `**生成中** ${index}\n<script>alert(1)</script>` });
    }
    expect(render).not.toHaveBeenCalled();
    expect(wrapper.find("script").exists()).toBe(false);
    expect(wrapper.text()).toContain("**生成中** 49");
    await wrapper.setProps({ content: "**完成**\n\n```js\nconst n = 1;\n```\n\n$x^2$", streaming: false });
    expect(render).toHaveBeenCalledTimes(1);
    expect(wrapper.find("strong").text()).toBe("完成");
    expect(wrapper.find(".assistant-code-block").exists()).toBe(true);
    expect(wrapper.find(".katex").exists()).toBe(true);
    wrapper.unmount();
  });

  it("restores code copying when streaming switches to the formatted answer", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const content = "```js\nconst n = 1;\n```\n[Framework]internal trace";
    const wrapper = mount(AssistantMarkdown, { props: { content, streaming: true } });
    expect(wrapper.text()).not.toContain("internal trace");
    expect(wrapper.find("[data-code-copy]").exists()).toBe(false);
    await wrapper.setProps({ streaming: false });
    const button = wrapper.get("[data-code-copy]");
    await button.trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith("const n = 1;\n");
    expect(button.text()).toBe("已复制");
    await vi.advanceTimersByTimeAsync(1400);
    expect(button.text()).toBe("复制");
    wrapper.unmount();
  });
});
