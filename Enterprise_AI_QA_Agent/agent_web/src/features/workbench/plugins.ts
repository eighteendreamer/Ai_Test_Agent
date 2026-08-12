import { markRaw, type Component } from "vue";

import ConversationWorkbenchPlugin from "./plugins/ConversationWorkbenchPlugin.vue";
import IdleWorkbenchPlugin from "./plugins/IdleWorkbenchPlugin.vue";

export type WorkbenchPluginKey = "idle" | "conversation";

export interface WorkbenchPluginDefinition {
  key: WorkbenchPluginKey;
  component: Component;
}

export const workbenchPlugins: Record<WorkbenchPluginKey, WorkbenchPluginDefinition> = {
  idle: {
    key: "idle",
    component: markRaw(IdleWorkbenchPlugin),
  },
  conversation: {
    key: "conversation",
    component: markRaw(ConversationWorkbenchPlugin),
  },
};
