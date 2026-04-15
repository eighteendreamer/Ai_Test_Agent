<template>
  <div class="p-6">
    <n-card title="8Manage PM 配置" class="shadow-sm">
      <n-space vertical :size="16">
        <n-alert type="info" :bordered="false">
          8Manage PM 已接入远端项目同步。推荐在“平台总控制台”里统一配置地址、鉴权方式、项目接口路径和字段映射。
        </n-alert>
        <n-alert type="warning" :bordered="false">
          如果你们使用的是私有化 API，请把“项目接口路径”“列表结果路径”“字段映射”改成实际接口定义后再测试连接。
        </n-alert>
        <n-space>
          <router-link to="/project/control">
            <n-button type="primary">前往平台总控制台</n-button>
          </router-link>
          <router-link to="/project/8manage/cases">
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
