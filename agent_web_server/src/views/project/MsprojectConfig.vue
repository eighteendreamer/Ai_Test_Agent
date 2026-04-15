<template>
  <div class="p-6">
    <n-card title="Microsoft Project 配置" class="shadow-sm">
      <n-space vertical :size="16">
        <n-alert type="info" :bordered="false">
          Microsoft Project 已接入远端项目同步。默认按 Microsoft Graph Planner 接口预填，也支持你替换成自定义项目列表接口。
        </n-alert>
        <n-alert type="warning" :bordered="false">
          如果你不是直接对接 Microsoft Graph，请到“平台总控制台”里修改接口路径、结果列表路径和字段映射。
        </n-alert>
        <n-space>
          <router-link to="/project/control">
            <n-button type="primary">前往平台总控制台</n-button>
          </router-link>
          <router-link to="/project/msproject/cases">
            <n-button secondary type="primary">查看远端项目同步</n-button>
          </router-link>
        </n-space>
      </n-space>
    </n-card>
  </div>
</template>

<script setup>
import { NAlert, NButton, NCard, NSpace } from 'naive-ui'
</script>
