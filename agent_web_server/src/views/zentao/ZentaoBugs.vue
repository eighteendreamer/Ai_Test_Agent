<template>
  <div class="zentao-bugs-container">
    <n-card title="🐛 禅道 Bug 管理" :bordered="false">
      <template #header-extra>
        <n-space>
          <n-button :loading="syncing" @click="handleSyncAll">
            <template #icon><i class="fas fa-sync-alt"></i></template>
            同步全部状态
          </n-button>
        </n-space>
      </template>

      <n-alert type="info" class="mb-4">
        将本系统发现的 Bug 推送到禅道，并从禅道同步 Bug 的最新状态（激活/已解决/已关闭）。
      </n-alert>

      <!-- 筛选栏 -->
      <n-space class="mb-4" align="center">
        <n-select
          v-model:value="filterStatus"
          placeholder="筛选状态"
          clearable
          style="width: 130px"
          :options="[
            { label: '待处理', value: '待处理' },
            { label: '已确认', value: '已确认' },
            { label: '已修复', value: '已修复' },
            { label: '已关闭', value: '已关闭' },
          ]"
          @update:value="handleFilterChange"
        />
        <n-select
          v-model:value="filterSeverity"
          placeholder="筛选严重程度"
          clearable
          style="width: 140px"
          :options="[
            { label: '一级', value: '一级' },
            { label: '二级', value: '二级' },
            { label: '三级', value: '三级' },
            { label: '四级', value: '四级' },
          ]"
          @update:value="handleFilterChange"
        />
        <n-tag v-if="pagination.itemCount > 0" type="default" size="small">
          共 {{ pagination.itemCount }} 条
        </n-tag>
      </n-space>

      <!-- Bug 列表 -->
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

      <!-- 底部批量操作 -->
      <div v-if="checkedKeys.length > 0" class="batch-bar mt-4">
        <n-space align="center">
          <span class="text-sm text-gray-500">已选 {{ checkedKeys.length }} 条</span>
          <n-button type="primary" size="small" :loading="pushing" @click="handleBatchPush">
            批量推送到禅道
          </n-button>
        </n-space>
      </div>
    </n-card>

    <!-- 推送选项弹窗 -->
    <n-modal v-model:show="showPushModal">
      <n-card style="width: 420px" title="推送 Bug 到禅道" :bordered="false" size="huge" role="dialog" aria-modal="true">
        <n-form label-placement="left" label-width="100">
          <n-form-item label="产品ID">
            <n-input-number v-model:value="pushForm.product_id" :min="1" style="width:100%" placeholder="留空则使用默认产品" clearable />
          </n-form-item>
          <n-form-item label="严重程度">
            <n-select v-model:value="pushForm.severity" :options="severityOptions" />
          </n-form-item>
          <n-form-item label="优先级">
            <n-select v-model:value="pushForm.pri" :options="priOptions" />
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
import { ref, h, onMounted, reactive } from 'vue'
import { useMessage, NButton, NTag, NSpace, NTooltip } from 'naive-ui'
import { bugReportAPI } from '@/api/index'
import { zentaoAPI } from '@/api/zentao'

const message = useMessage()

const loading = ref(false)
const pushing = ref(false)
const syncing = ref(false)
const bugs = ref([])
const checkedKeys = ref([])
const showPushModal = ref(false)
const pendingPushIds = ref([])

const filterStatus = ref(null)
const filterSeverity = ref(null)

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

const pushForm = ref({ product_id: null, severity: 3, pri: 3 })

const severityOptions = [
  { label: '1级 - 致命', value: 1 },
  { label: '2级 - 严重', value: 2 },
  { label: '3级 - 一般', value: 3 },
  { label: '4级 - 建议', value: 4 }
]
const priOptions = [
  { label: '1 - 紧急', value: 1 },
  { label: '2 - 高', value: 2 },
  { label: '3 - 中', value: 3 },
  { label: '4 - 低', value: 4 }
]

const columns = [
  { type: 'selection' },
  { title: 'ID', key: 'id', width: 60 },
  { title: 'Bug 名称', key: 'bug_name', ellipsis: { tooltip: true } },
  { title: '错误类型', key: 'error_type', width: 100 },
  {
    title: '严重程度', key: 'severity_level', width: 90,
    render: (row) => {
      const map = { '一级': 'error', '二级': 'warning', '三级': 'info', '四级': 'default' }
      return h(NTag, { type: map[row.severity_level] || 'default', size: 'small' }, () => row.severity_level)
    }
  },
  {
    title: '本地状态', key: 'status', width: 90,
    render: (row) => {
      const map = { '待处理': 'warning', '已确认': 'info', '已修复': 'success', '已关闭': 'default' }
      return h(NTag, { type: map[row.status] || 'default', size: 'small' }, () => row.status)
    }
  },
  {
    title: '禅道ID', key: 'zentao_bug_id', width: 90,
    render: (row) => row.zentao_bug_id
      ? h(NTag, { type: 'success', size: 'small' }, () => `#${row.zentao_bug_id}`)
      : h('span', { style: 'color: #9ca3af; font-size: 12px' }, '未推送')
  },
  { title: '创建时间', key: 'created_at', width: 170 },
  {
    title: '操作', key: 'actions', width: 200,
    render: (row) => h(NSpace, { size: 'small' }, () => [
      !row.zentao_bug_id && h(NButton, {
        size: 'small', type: 'primary',
        onClick: () => { pendingPushIds.value = [row.id]; showPushModal.value = true }
      }, () => '推送'),
      row.zentao_bug_id && h(NButton, {
        size: 'small', type: 'info',
        loading: syncing.value,
        onClick: () => handleSyncOne(row.id)
      }, () => '同步状态')
    ])
  }
]

async function loadBugs() {
  loading.value = true
  try {
    const projectId = parseInt(localStorage.getItem('currentProjectId')) || null
    const res = await bugReportAPI.getList({
      limit: pagination.pageSize,
      offset: (pagination.page - 1) * pagination.pageSize,
      status: filterStatus.value || undefined,
      severity: filterSeverity.value || undefined,
      project_id: projectId || undefined
    })
    // axios 拦截器已返回 response.data，即 { success, data, total }
    bugs.value = res.data || []
    pagination.itemCount = res.total || 0
  } catch (e) {
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
    const bug = bugs.value.find(b => b.id === id)
    return bug && !bug.zentao_bug_id
  })
  if (unpushed.length === 0) {
    message.warning('所选 Bug 均已推送到禅道')
    return
  }
  pendingPushIds.value = unpushed
  showPushModal.value = true
}

async function confirmPush() {
  pushing.value = true
  try {
    const res = await zentaoAPI.pushBugs(
      pendingPushIds.value,
      pushForm.value.product_id,
      pushForm.value.severity,
      pushForm.value.pri
    )
    const data = res.data
    if (data.success !== false) {
      message.success(data.message || `推送完成：成功 ${data.success_count ?? 1}，失败 ${data.failed_count ?? 0}`)
    } else {
      message.warning(data.message || '推送失败')
    }
    showPushModal.value = false
    checkedKeys.value = []
    loadBugs()
  } catch (e) {
    message.error(e.response?.data?.detail || '推送失败')
  } finally {
    pushing.value = false
  }
}

async function handleSyncOne(bugId) {
  syncing.value = true
  try {
    const res = await zentaoAPI.syncBugStatus(bugId)
    message.success(res.data?.message || '同步完成')
    loadBugs()
  } catch (e) {
    message.error('同步失败')
  } finally {
    syncing.value = false
  }
}

async function handleSyncAll() {
  syncing.value = true
  try {
    const res = await zentaoAPI.syncBugStatus()
    message.success(res.data?.message || '同步完成')
    loadBugs()
  } catch (e) {
    message.error(e.response?.data?.detail || '同步失败')
  } finally {
    syncing.value = false
  }
}

onMounted(loadBugs)
</script>

<style scoped>
.batch-bar {
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}
</style>
