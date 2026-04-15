<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <n-dialog-provider>
        <MainLayout />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { NConfigProvider, NMessageProvider, NDialogProvider } from 'naive-ui'
import MainLayout from './layouts/MainLayout.vue'

const themeOverrides = {
  common: {
    primaryColor: '#007857',
    primaryColorHover: '#005c43',
    primaryColorPressed: '#004d38'
  }
}
</script>

<style>
#app {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}
</style>
