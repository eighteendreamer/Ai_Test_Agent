<template>
  <div class="platform-remote-projects p-6">
    <n-card :title="`${platformName} 项目同步`" class="shadow-sm">
      <template #header-extra>
        <n-button size="small" secondary type="primary" :loading="loading" @click="loadProjects">
          <template #icon>
            <i class="fas fa-sync-alt"></i>
          </template>
          刷新列表
        </n-button>
      </template>

      <n-alert type="info" class="mb-4" :bordered="false">
        这里展示的是 {{ platformName }} 的远端项目/空间。你可以先把它们同步到本地“项目管理”，后续再继续补用例导入。
      </n-alert>

      <div v-if="!loading && projects.length === 0" class="py-8">
        <n-empty description="暂无可同步项目，请先检查平台配置和授权状态">
          <template #icon>
            <i class="fas fa-folder-open text-4xl text-gray-400"></i>
          </template>
        </n-empty>
      </div>

      <n-data-table
        v-else
        :columns="columns"
        :data="projects"
        :loading="loading"
        :bordered="false"
        striped
      />
    </n-card>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { NAlert, NButton, NButtonGroup, NDataTable, NEmpty, NTag, useMessage } from 'naive-ui'
import { getRemoteProjects } from '@/api/project'
import { importPlatformProject } from '@/api/testProject'

const props = defineProps({
  platformId: {
    type: String,
    required: true
  },
  platformName: {
    type: String,
    required: true
  }
})

const message = useMessage()
const loading = ref(false)
const projects = ref([])
const importingId = ref('')

const columns = [
  {
    title: 'ID',
    key: 'id',
    width: 140,
    ellipsis: { tooltip: true }
  },
  {
    title: '项目名称',
    key: 'name',
    ellipsis: { tooltip: true }
  },
  {
    title: '代号',
    key: 'code',
    width: 140,
    ellipsis: { tooltip: true }
  },
  {
    title: '范围',
    key: 'scope',
    width: 180,
    ellipsis: { tooltip: true },
    render: (row) => row.scope || '-'
  },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: (row) => h(
      NTag,
      {
        size: 'small',
        type: row.status ? 'default' : 'warning'
      },
      {
        default: () => row.status || '未知'
      }
    )
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: (row) => h(
      NButtonGroup,
      { size: 'small' },
      {
        default: () => [
          h(NButton, {
            type: 'primary',
            secondary: true,
            loading: importingId.value === row.id,
            onClick: () => handleImportProject(row)
          }, () => '导入项目')
        ]
      }
    )
  }
]

async function loadProjects() {
  loading.value = true
  try {
    const res = await getRemoteProjects(props.platformId)
    projects.value = Array.isArray(res.data) ? res.data : []
  } catch (error) {
    projects.value = []
    message.error(error.response?.data?.detail || `获取 ${props.platformName} 项目列表失败`)
  } finally {
    loading.value = false
  }
}

async function handleImportProject(project) {
  importingId.value = project.id
  try {
    const descriptionParts = [
      `从 ${props.platformName} 同步的远端项目。`
    ]
    if (project.scope) descriptionParts.push(`范围: ${project.scope}`)
    if (project.description) descriptionParts.push(project.description)

    const res = await importPlatformProject({
      platform_id: props.platformId,
      source_id: project.id,
      source_name: project.name,
      source_code: project.code || '',
      description: descriptionParts.join('\n')
    })
    message.success(res.message || '项目导入成功')
  } catch (error) {
    message.error(error.response?.data?.detail || `导入 ${props.platformName} 项目失败`)
  } finally {
    importingId.value = ''
  }
}

onMounted(loadProjects)
</script>

<style scoped>
.platform-remote-projects {
  max-width: 1400px;
}
</style>
