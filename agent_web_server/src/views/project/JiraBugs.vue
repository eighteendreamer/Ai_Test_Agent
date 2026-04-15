<template>
  <div class="jira-bugs-container p-6">
    <n-card title="Jira Bug 推送与同步" class="shadow-sm">
      <template #header-extra>
        <n-space>
          <n-button secondary type="primary" :loading="loadingProjects" @click="loadProjects">
            <template #icon>
              <i class="fas fa-folder-open"></i>
            </template>
            刷新项目
          </n-button>
          <n-button :loading="syncing" @click="handleSyncAll">
            <template #icon>
              <i class="fas fa-sync-alt"></i>
            </template>
            同步全部状态
          </n-button>
        </n-space>
      </template>

      <n-alert type="info" class="mb-4" :bordered="false">
        将本系统中的 Bug 推送到 Jira Issue，并把 Jira 的状态同步回本地。
      </n-alert>

      <n-space class="mb-4" align="center">
        <n-select
          v-model:value="filterStatus"
          placeholder="筛选状态"
          clearable
          style="width: 140px"
          :options="statusOptions"
          @update:value="handleFilterChange"
        />
        <n-select
          v-model:value="filterSeverity"
          placeholder="筛选严重程度"
          clearable
          style="width: 160px"
          :options="severityOptions"
          @update:value="handleFilterChange"
        />
        <n-tag v-if="pagination.itemCount > 0" size="small" type="default">
          共 {{ pagination.itemCount }} 条
        </n-tag>
      </n-space>

      <n-data-table
        remote
        :columns="columns"
        :data="bugs"
        :loading="loading"
        :bordered="false"
        :row-key="row => row.id"
        v-model:checked-row-keys="checkedKeys"
        :pagination="pagination"
      />

      <div v-if="checkedKeys.length > 0" class="batch-bar mt-4">
        <n-space align="center">
          <span class="text-sm text-gray-500">已选择 {{ checkedKeys.length }} 条</span>
          <n-button type="primary" size="small" :loading="pushing" @click="handleBatchPush">
            批量推送到 Jira
          </n-button>
        </n-space>
      </div>
    </n-card>

    <n-modal v-model:show="showPushModal">
      <n-card title="推送 Bug 到 Jira" style="width: 520px" :bordered="false" size="huge" role="dialog" aria-modal="true">
        <n-form label-placement="left" label-width="100">
          <n-form-item label="Jira 项目">
            <n-select
              v-model:value="pushForm.project_key"
              filterable
              placeholder="请选择 Jira 项目"
              :options="projectOptions"
            />
          </n-form-item>
          <n-form-item label="Issue 类型">
            <n-input v-model:value="pushForm.issue_type" placeholder="默认 Bug，可改为 Defect 等" />
          </n-form-item>
          <n-form-item label="优先级">
            <n-select
              v-model:value="pushForm.priority_name"
              clearable
              placeholder="可选"
              :options="priorityOptions"
            />
          </n-form-item>
        </n-form>
        <template #footer>
          <n-space justify="end">
            <n-button @click="showPushModal = false">取消</n-button>
            <n-button type="primary" :loading="pushing" @click="confirmPush">确认推送</n-button>
          </n-space>
        </template>
      </n-card>
    </n-modal>
  </div>
</template>

<script setup>
import { computed, h, onMounted, reactive, ref } from 'vue'
import { NButton, NInput, NSpace, NTag, useMessage } from 'naive-ui'
import { bugReportAPI } from '@/api/index'
import { jiraAPI } from '@/api/jira'
import { getRemoteProjects } from '@/api/project'

const message = useMessage()

const loading = ref(false)
const loadingProjects = ref(false)
const pushing = ref(false)
const syncing = ref(false)
const bugs = ref([])
const checkedKeys = ref([])
const showPushModal = ref(false)
const pendingPushIds = ref([])
const jiraProjects = ref([])

const filterStatus = ref(null)
const filterSeverity = ref(null)

const statusOptions = [
  { label: '待处理', value: '待处理' },
  { label: '已确认', value: '已确认' },
  { label: '已修复', value: '已修复' },
  { label: '已关闭', value: '已关闭' }
]

const severityOptions = [
  { label: '一级', value: '一级' },
  { label: '二级', value: '二级' },
  { label: '三级', value: '三级' },
  { label: '四级', value: '四级' }
]

const priorityOptions = [
  { label: 'Highest', value: 'Highest' },
  { label: 'High', value: 'High' },
  { label: 'Medium', value: 'Medium' },
  { label: 'Low', value: 'Low' },
  { label: 'Lowest', value: 'Lowest' }
]

const pagination = reactive({
  page: 1,
  pageSize: 20,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50, 100],
  prefix: ({ itemCount }) => `共 ${itemCount} 条`,
  onChange: (page) => {
    pagination.page = page
    loadBugs()
  },
  onUpdatePageSize: (pageSize) => {
    pagination.pageSize = pageSize
    pagination.page = 1
    loadBugs()
  }
})

const pushForm = ref({
  project_key: null,
  issue_type: 'Bug',
  priority_name: null
})

const projectOptions = computed(() =>
  jiraProjects.value.map(project => ({
    label: `${project.name} (${project.code || project.id})`,
    value: project.code || project.id
  }))
)

const columns = [
  { type: 'selection' },
  { title: 'ID', key: 'id', width: 70 },
  { title: 'Bug 名称', key: 'bug_name', ellipsis: { tooltip: true } },
  { title: '错误类型', key: 'error_type', width: 120 },
  {
    title: '严重程度',
    key: 'severity_level',
    width: 100,
    render: (row) => h(
      NTag,
      { size: 'small', type: severityTagType(row.severity_level) },
      { default: () => row.severity_level || '-' }
    )
  },
  {
    title: '本地状态',
    key: 'status',
    width: 110,
    render: (row) => h(
      NTag,
      { size: 'small', type: statusTagType(row.status) },
      { default: () => row.status || '-' }
    )
  },
  {
    title: 'Jira Issue',
    key: 'jira_issue_key',
    width: 130,
    render: (row) => row.jira_issue_key
      ? h(NTag, { type: 'success', size: 'small' }, { default: () => row.jira_issue_key })
      : h('span', { style: 'color: #9ca3af; font-size: 12px' }, '未推送')
  },
  { title: '创建时间', key: 'created_at', width: 180 },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) => h(NSpace, { size: 'small' }, {
      default: () => [
        !row.jira_issue_key && h(NButton, {
          size: 'small',
          type: 'primary',
          onClick: () => {
            pendingPushIds.value = [row.id]
            showPushModal.value = true
          }
        }, { default: () => '推送' }),
        row.jira_issue_key && h(NButton, {
          size: 'small',
          type: 'info',
          loading: syncing.value,
          onClick: () => handleSyncOne(row.id)
        }, { default: () => '同步状态' })
      ]
    })
  }
]

function severityTagType(level) {
  return {
    一级: 'error',
    二级: 'warning',
    三级: 'info',
    四级: 'default'
  }[level] || 'default'
}

function statusTagType(status) {
  return {
    待处理: 'warning',
    已确认: 'info',
    已修复: 'success',
    已关闭: 'default'
  }[status] || 'default'
}

async function loadProjects() {
  loadingProjects.value = true
  try {
    const res = await getRemoteProjects('jira')
    jiraProjects.value = Array.isArray(res.data) ? res.data : []
    if (!pushForm.value.project_key && jiraProjects.value.length > 0) {
      pushForm.value.project_key = jiraProjects.value[0].code || jiraProjects.value[0].id
    }
  } catch (error) {
    jiraProjects.value = []
    message.error(error.response?.data?.detail || '加载 Jira 项目失败')
  } finally {
    loadingProjects.value = false
  }
}

async function loadBugs() {
  loading.value = true
  try {
    const res = await bugReportAPI.getList({
      limit: pagination.pageSize,
      offset: (pagination.page - 1) * pagination.pageSize,
      status: filterStatus.value || undefined,
      severity: filterSeverity.value || undefined
    })
    bugs.value = res.data || []
    pagination.itemCount = res.total || 0
  } catch (error) {
    message.error('加载 Bug 列表失败')
  } finally {
    loading.value = false
  }
}

function handleFilterChange() {
  pagination.page = 1
  loadBugs()
}

function handleBatchPush() {
  const unpushed = checkedKeys.value.filter(id => {
    const bug = bugs.value.find(item => item.id === id)
    return bug && !bug.jira_issue_key
  })
  if (unpushed.length === 0) {
    message.warning('所选 Bug 都已推送到 Jira')
    return
  }
  pendingPushIds.value = unpushed
  showPushModal.value = true
}

async function confirmPush() {
  if (!pushForm.value.project_key) {
    message.warning('请选择 Jira 项目')
    return
  }

  pushing.value = true
  try {
    const res = await jiraAPI.pushBugs(
      pendingPushIds.value,
      pushForm.value.project_key,
      pushForm.value.issue_type || 'Bug',
      pushForm.value.priority_name
    )
    const data = res.data
    if (data.success !== false) {
      message.success(data.message || `推送完成：成功 ${data.success_count ?? 1}，失败 ${data.failed_count ?? 0}`)
    } else {
      message.warning(data.message || '推送失败')
    }
    showPushModal.value = false
    checkedKeys.value = []
    await loadBugs()
  } catch (error) {
    message.error(error.response?.data?.detail || '推送到 Jira 失败')
  } finally {
    pushing.value = false
  }
}

async function handleSyncOne(bugId) {
  syncing.value = true
  try {
    const res = await jiraAPI.syncBugStatus(bugId)
    message.success(res.data?.message || '同步完成')
    await loadBugs()
  } catch (error) {
    message.error(error.response?.data?.detail || '同步失败')
  } finally {
    syncing.value = false
  }
}

async function handleSyncAll() {
  syncing.value = true
  try {
    const res = await jiraAPI.syncBugStatus()
    message.success(res.data?.message || '同步完成')
    await loadBugs()
  } catch (error) {
    message.error(error.response?.data?.detail || '同步失败')
  } finally {
    syncing.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadProjects(), loadBugs()])
})
</script>

<style scoped>
.batch-bar {
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}
</style>
