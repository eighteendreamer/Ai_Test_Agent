// @vitest-environment jsdom
import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import enUS from "../../locales/en-US";
import zhCN from "../../locales/zh-CN";
import { registerLocale, setLocale } from "../../services/i18n";
import type { ChatMessage } from "../../types";
import ChatTimeline from "./ChatTimeline.vue";


function assistantMessage(mode: string): ChatMessage {
  return {
    id: `assistant-${mode}`,
    role: "assistant",
    content: "Model authentication failed. Check its credentials and retry.",
    created_at: "2026-09-30T08:00:00Z",
    metadata: {
      response_mode: mode,
      transcript_bucket: mode === "ok" ? "conversation" : "error",
    },
  };
}


describe("model failure transcript", () => {
  beforeEach(() => {
    registerLocale("zh-CN", zhCN);
    registerLocale("en-US", enUS);
    setLocale("zh-CN");
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("labels a persisted model failure instead of presenting it as a normal answer", () => {
    const failed = mount(ChatTimeline, { props: { messages: [assistantMessage("auth_error")] } });
    const normal = mount(ChatTimeline, { props: { messages: [assistantMessage("ok")] } });

    expect(failed.find(".chat-kind-chip").text()).toBe("模型调用失败");
    expect(failed.find(".chat-assistant-message").classes()).toContain("is-model-failure");
    expect(normal.find(".chat-kind-chip").exists()).toBe(false);
    expect(normal.find(".chat-assistant-message").classes()).not.toContain("is-model-failure");

    failed.unmount();
    normal.unmount();
  });
});
