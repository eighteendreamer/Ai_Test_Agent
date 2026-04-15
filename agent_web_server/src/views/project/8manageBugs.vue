<template>
  <div class="p-6">
    <n-card title="8Manage PMBug推送与同步" class="shadow-sm">
      <n-alert type="warning" class="mb-4">
        <template #icon>
          <i class="fas fa-exclamation-triangle"></i>
        </template>
        请先在"8Manage PM配置"中完成系统连接配置
      </n-alert>
      
      <n-empty
        description="Bug推送与同步功能开发中..."
        class="py-12"
      >
        <template #icon>
          <i class="fas fa-bug text-4xl text-gray-400"></i>
        </template>
        <template #extra>
          <n-space>
            <n-button type="primary" disabled>
              <template #icon>
                <i class="fas fa-upload"></i>
              </template>
              推送Bug
            </n-button>
            <n-button disabled>
              <template #icon>
                <i class="fas fa-sync"></i>
              </template>
              同步状态
            </n-button>
          </n-space>
        </template>
      </n-empty>
    </n-card>
  </div>
</template>

<script setup>
import { NCard, NAlert, NEmpty, NSpace, NButton } from 'naive-ui'
</script>
