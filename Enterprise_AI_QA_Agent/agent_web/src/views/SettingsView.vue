<script setup lang="ts">
import { computed, ref } from "vue";

import { settingsPlugins, type SettingsPluginDefinition, type SettingsPluginKey } from "../features/settings/plugins";

const defaultPlugin = settingsPlugins.find((item) => !item.reserved) ?? settingsPlugins[0];
const activePluginKey = ref<SettingsPluginKey>(defaultPlugin.key);

const activePlugin = computed(
  () => settingsPlugins.find((item) => item.key === activePluginKey.value) ?? defaultPlugin,
);

function selectPlugin(plugin: SettingsPluginDefinition) {
  if (plugin.reserved) {
    return;
  }
  activePluginKey.value = plugin.key;
}
</script>

<template>
  <section class="view-page settings-page settings-shell">
    <div class="settings-frame">
      <div class="settings-workspace">
        <aside class="settings-section-nav">
          <button
            v-for="plugin in settingsPlugins"
            :key="plugin.key"
            type="button"
            class="settings-section-btn"
            :class="{ active: activePluginKey === plugin.key, reserved: plugin.reserved }"
            :disabled="plugin.reserved"
            @click="selectPlugin(plugin)"
          >
            <div class="settings-section-btn-head">
              <strong>{{ plugin.label }}</strong>
            </div>
          </button>
        </aside>

        <main class="settings-mainpanel">
          <component :is="activePlugin.component" :plugin="activePlugin" />
        </main>
      </div>
    </div>
  </section>
</template>
