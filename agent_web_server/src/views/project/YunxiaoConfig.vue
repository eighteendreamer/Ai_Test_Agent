<template>
  <div class="p-6">
    <n-card title="云效配置" class="shadow-sm">
      <n-alert type="info" class="mb-4">
        <template #icon>
          <i class="fas fa-info-circle"></i>
        </template>
        阿里云云效是一站式研发协同平台，提供项目管理、代码托管、持续集成等服务。
      </n-alert>
      
      <n-empty
        description="云效 集成功能开发中..."
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
