<script setup lang="ts">
import type { SettingsPluginDefinition } from "../plugins";

defineProps<{
  plugin?: SettingsPluginDefinition;
}>();
</script>

<template>
  <section class="settings-pane">
    <div class="settings-pane-head">
      <div>
        <h3>{{ plugin?.label ?? "预留设置" }}</h3>
        <p>该设置项已预留为插件入口，当前尚未接入独立的后端设置接口。</p>
      </div>
    </div>

    <div class="settings-empty">当前仅保留入口位，不展示任何写死的配置内容。</div>
  </section>
</template>
