<template>
  <div class="p-6">
    <n-card title="ClickUp配置" class="shadow-sm">
      <n-alert type="info" class="mb-4">
        <template #icon>
          <i class="fas fa-info-circle"></i>
        </template>
        ClickUp 是一款全能型项目管理工具，集成任务管理、文档协作、时间跟踪等功能。
      </n-alert>
      
      <n-empty
        description="ClickUp 集成功能开发中..."
        class="py-12"
      >
        <template #icon>
          <i class="fas fa-tools text-4xl text-gray-400"></i>
        </template>
        <template #extra>
          <n-space>
            <n-button type="primary" disabled>
              <template #icon>
                <i class="fas fa-plug"></i>
              </template>
              配置连接
            </n-button>
            <n-button disabled>
              <template #icon>
                <i class="fas fa-book"></i>
              </template>
              查看文档
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
