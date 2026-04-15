<template>
  <div class="p-6">
    <n-card title="Asana配置" class="shadow-sm">
      <n-alert type="info" class="mb-4">
        <template #icon>
          <i class="fas fa-info-circle"></i>
        </template>
        Asana 是一款团队协作和项目管理工具，帮助团队组织、跟踪和管理工作。
      </n-alert>
      
      <n-empty
        description="Asana 集成功能开发中..."
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
