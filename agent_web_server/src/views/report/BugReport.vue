<template>
  <div class="bug-report-view">
    <!-- 页面说明 -->
    <n-card>
      <template #header>
        <div class="flex items-center gap-2">
          <i class="fas fa-bug text-xl text-primary"></i>
          <span class="text-lg font-bold">Bug 测试报告</span>
        </div>
      </template>
      <p class="text-gray-500">
        管理测试执行过程中发现的 Bug，包括 Bug 详情、严重程度、处理状态等
      </p>
    </n-card>

    <!-- 筛选区域 -->
    <n-card style="margin-top: 20px">
      <n-form inline :model="filters" label-placement="left">
        <n-form-item label="严重程度">
          <n-select 
            v-model:value="filters.severity" 
            :options="severityOptions"
            clearable
            placeholder="全部"
            style="width: 120px"
          />
        </n-form-item>
        <n-form-item label="状态">
          <n-select 
            v-model:value="filters.status" 
            :options="statusOptions"
            clearable
            placeholder="全部"
            style="width: 120px"
          />
        </n-form-item>
        <n-form-item label="搜索">
          <n-input 
            v-model:value="filters.search" 
            placeholder="搜索 Bug 标题或描述"
            clearable
          />
        </n-form-item>
      </n-form>
    </n-card>

    <!-- Bug 统计卡片 -->
    <n-grid :cols="4" :x-gap="20" style="margin-top: 20px">
      <n-gi>
        <n-card size="small" class="stat-card">
          <n-statistic label="总计" :value="stats.total">
            <template #prefix>
              <i class="fas fa-bug"></i>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card stat-critical">
          <n-statistic label="严重" :value="stats.critical">
            <template #prefix>
              <i class="fas fa-exclamation-circle"></i>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card stat-open">
          <n-statistic label="待处理" :value="stats.open">
            <template #prefix>
              <i class="fas fa-clock"></i>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card stat-fixed">
          <n-statistic label="已修复" :value="stats.fixed">
            <template #prefix>
              <i class="fas fa-check-circle"></i>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- Bug 列表 -->
    <n-card style="margin-top: 20px">
      <template #header>
        <span class="font-bold">Bug 列表</span>
      </template>

      <n-data-table
        :columns="columns"
        :data="bugs"
        :loading="loading"
        :row-key="row => row.id"
        striped
      />

      <!-- 分页 -->
      <div style="margin-top: 20px; display: flex; justify-content: flex-end;">
        <n-pagination
          v-model:page="currentPage"
          v-model:page-size="pageSize"
          :item-count="total"
          :page-sizes="[10, 20, 50]"
          show-size-picker
          @update:page="loadBugs"
          @update:page-size="handlePageSizeChange"
        />
      </div>
    </n-card>

    <!-- Bug 详情对话框 -->
    <n-modal 
      v-model:show="detailDialogVisible" 
      preset="card" 
      title="Bug 详情" 
      style="width: 900px"
    >
      <n-spin :show="detailLoading">
        <div v-if="currentBug">
          <!-- 基本信息 -->
          <n-card size="small" style="margin-bottom: 20px">
            <template #header>
              <div class="flex items-center justify-between">
                <span><strong>{{ currentBug.bug_name }}</strong></span>
                <n-space>
                  <n-tag :type="getSeverityType(currentBug.severity_level)" size="small">
                    {{ currentBug.severity_level }}
                  </n-tag>
                  <n-tag :type="getStatusType(currentBug.status)" size="small">
                    {{ formatStatus(currentBug.status) }}
                  </n-tag>
                </n-space>
              </div>
            </template>
            <n-descriptions :column="2" label-placement="left" bordered>
              <n-descriptions-item label="Bug ID">{{ currentBug.id }}</n-descriptions-item>
              <n-descriptions-item label="关联测试用例">
                {{ currentBug.test_case_id ? `用例 #${currentBug.test_case_id}` : '-' }}
              </n-descriptions-item>
              <n-descriptions-item label="测试类型">
                <n-tag :type="getCaseTypeTag(currentBug.case_type)" size="small">
                  {{ currentBug.case_type || '功能测试' }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="执行模式">
                <n-tag :type="currentBug.execution_mode === '批量' ? 'warning' : 'default'" size="small">
                  {{ currentBug.execution_mode || '单量' }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="发现时间">{{ currentBug.created_at }}</n-descriptions-item>
              <n-descriptions-item label="更新时间">{{ currentBug.updated_at || '-' }}</n-descriptions-item>
              <n-descriptions-item label="错误类型">{{ currentBug.error_type || '-' }}</n-descriptions-item>
              <n-descriptions-item label="定位地址">{{ currentBug.location_url || '-' }}</n-descriptions-item>
            </n-descriptions>
          </n-card>

          <!-- Bug 描述 -->
          <n-card size="small" style="margin-bottom: 20px">
            <template #header>
              <strong>问题描述</strong>
            </template>
            <div class="bug-description" style="white-space: pre-line;">
              {{ currentBug.description || `测试用例【${currentBug.bug_name}】执行失败` }}
            </div>
          </n-card>

          <!-- 复现步骤 -->
          <n-card v-if="currentBug.reproduce_steps && currentBug.reproduce_steps.length > 0" size="small" style="margin-bottom: 20px">
            <template #header>
              <strong>{{ isConsolidatedBug(currentBug) ? '各Bug复现步骤' : '复现步骤' }}</strong>
            </template>
            <div class="bug-steps">
              <!-- 整合报告：按段落展示 -->
              <div v-if="isConsolidatedBug(currentBug)" style="white-space: pre-line; line-height: 1.8;">
                {{ typeof currentBug.reproduce_steps === 'string' ? currentBug.reproduce_steps : currentBug.reproduce_steps.join('\n') }}
              </div>
              <!-- 普通报告：列表展示 -->
              <ol v-else>
                <li v-for="(step, index) in parseSteps(currentBug.reproduce_steps)" :key="index">
                  {{ step }}
                </li>
              </ol>
            </div>
          </n-card>

          <!-- 结果反馈 -->
          <n-card v-if="currentBug.result_feedback" size="small" style="margin-bottom: 20px">
            <template #header>
              <strong>结果反馈</strong>
            </template>
            <!-- 整合报告：多行文本展示 -->
            <div v-if="isConsolidatedBug(currentBug)">
              <div class="bug-description" style="margin-bottom: 12px;">{{ currentBug.result_feedback }}</div>
              <n-descriptions :column="1" label-placement="left" bordered>
                <n-descriptions-item label="预期结果">
                  <div style="white-space: pre-line;">{{ currentBug.expected_result || '-' }}</div>
                </n-descriptions-item>
                <n-descriptions-item label="实际结果">
                  <div style="white-space: pre-line;">{{ currentBug.actual_result || '-' }}</div>
                </n-descriptions-item>
              </n-descriptions>
            </div>
            <!-- 普通报告 -->
            <n-descriptions v-else :column="1" label-placement="left" bordered>
              <n-descriptions-item label="预期结果">{{ currentBug.expected_result || '-' }}</n-descriptions-item>
              <n-descriptions-item label="实际结果">{{ currentBug.actual_result || '-' }}</n-descriptions-item>
              <n-descriptions-item label="分析">{{ currentBug.result_feedback }}</n-descriptions-item>
            </n-descriptions>
          </n-card>

          <!-- 错误截图 -->
          <n-card v-if="currentBug.screenshot_path" size="small" style="margin-bottom: 20px">
            <template #header>
              <strong>错误截图</strong>
            </template>
            <n-image 
              :src="currentBug.screenshot_path"
              width="600"
              object-fit="contain"
            />
          </n-card>

          <!-- 原始数据 -->
          <n-card size="small">
            <template #header>
              <div class="flex items-center justify-between">
                <strong>原始数据</strong>
                <n-button size="tiny" secondary type="primary" @click="copyText(JSON.stringify(currentBug, null, 2))">
                  <template #icon><i class="fas fa-copy"></i></template> 复制
                </n-button>
              </div>
            </template>
            <div class="json-output-wrapper">
              <n-scrollbar style="max-height: 400px">
                <n-code :code="JSON.stringify(currentBug, null, 2)" language="json" show-line-numbers />
              </n-scrollbar>
            </div>
          </n-card>
        </div>
      </n-spin>
      
      <template #footer>
        <n-space justify="end">
          <n-button v-if="currentBug && currentBug.status !== 'fixed'" type="success" @click="markAsFixed">
            <template #icon>
              <i class="fas fa-check"></i>
            </template>
            标记为已修复
          </n-button>
          <n-button type="primary" @click="downloadBugReport">
            <template #icon>
              <i class="fas fa-download"></i>
            </template>
            下载报告
          </n-button>
          <n-button @click="detailDialogVisible = false">关闭</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, h, reactive } from 'vue'
import { 
  NCard, NButton, NDataTable, NModal, NDescriptions, NDescriptionsItem, 
  NTag, NForm, NFormItem, NInput, NSelect, NSpace, NGrid, NGi,
  NAlert, NSpin, NPagination, NStatistic, NImage, NImageGroup,
  NCode, NScrollbar, useMessage, useDialog
} from 'naive-ui'
import { bugReportAPI } from '@/api'
import { useLazyLoad } from '@/composables/useLazyLoad'

const message = useMessage()
const dialog = useDialog()

// 复制文本
const copyText = async (text) => {
  try {
    await navigator.clipboard.writeText(text)
    message.success('复制成功')
  } catch (err) {
    message.error('复制失败')
    console.error('Failed to copy:', err)
  }
}

// 统计数据
const stats = reactive({
  total: 0,
  critical: 0,
  open: 0,
  fixed: 0
})

// 更新统计数据
const updateStats = (data) => {
  stats.total = data.length
  stats.critical = data.filter(b => b.severity_level === '一级' || b.severity_level === '二级').length
  stats.open = data.filter(b =>
    ['open', 'in_progress', '待处理', '已确认', '处理中'].includes(b.status)
  ).length
  stats.fixed = data.filter(b =>
    ['fixed', 'closed', '已修复', '已关闭'].includes(b.status)
  ).length
}

// 筛选条件
const filters = reactive({
  severity: null,
  status: null,
  search: '',
  project_id: parseInt(localStorage.getItem('currentProjectId')) || null
})

// 包装 API 调用以适配 useLazyLoad
const fetchBugsWrapper = async (params) => {
  const result = await bugReportAPI.getList(params)
  if (result.success) {
    // 注意：这里的统计仅基于当前页数据，如果需要全局统计需要后端支持
    updateStats(result.data || [])
    return {
      success: true,
      data: result.data || [],
      total: result.total || (result.data ? result.data.length : 0)
    }
  }
  return result
}

// 使用懒加载
const {
  data: bugs,
  loading,
  currentPage,
  pageSize,
  total,
  refresh,
  goToPage,
  changePageSize: handlePageSizeChange
} = useLazyLoad({
  fetchFunction: fetchBugsWrapper,
  pageSize: 10,
  filters,
  autoLoad: true,
  debounceDelay: 500
})

const loadBugs = (page) => {
  if (typeof page === 'number') {
    goToPage(page)
  } else {
    refresh()
  }
}

// 严重程度选项
const severityOptions = [
  { label: '全部', value: null },
  { label: '一级（致命）', value: '一级' },
  { label: '二级（严重）', value: '二级' },
  { label: '三级（一般）', value: '三级' },
  { label: '四级（轻微）', value: '四级' }
]

// 状态选项（与后端保持中文一致）
const statusOptions = [
  { label: '全部', value: null },
  { label: '待处理', value: '待处理' },
  { label: '已确认', value: '已确认' },
  { label: '已修复', value: '已修复' },
  { label: '已关闭', value: '已关闭' }
]

// 详情对话框
const detailDialogVisible = ref(false)
const detailLoading = ref(false)
const currentBug = ref(null)

// 严重程度类型映射
const getSeverityType = (severity) => {
  const typeMap = {
    '一级': 'error',
    '二级': 'warning',
    '三级': 'info',
    '四级': 'default'
  }
  return typeMap[severity] || 'default'
}

// 测试类型标签映射
const getCaseTypeTag = (caseType) => {
  const typeMap = {
    '功能测试': 'info',
    '接口测试': 'warning',
    '单元测试': 'success',
    '性能测试': 'error',
    '安全测试': 'error'
  }
  return typeMap[caseType] || 'default'
}

// 状态类型映射（兼容中英文状态值）
const getStatusType = (status) => {
  const typeMap = {
    open: 'warning',
    in_progress: 'info',
    fixed: 'success',
    closed: 'default',
    待处理: 'warning',
    已确认: 'info',
    处理中: 'info',
    已修复: 'success',
    已关闭: 'default'
  }
  return typeMap[status] || 'default'
}

// 格式化严重程度（后端已返回中文，直接使用）
const formatSeverity = (severity) => {
  return severity || '-'
}

// 格式化状态（兼容中英文状态值）
const formatStatus = (status) => {
  const textMap = {
    open: '待处理',
    in_progress: '处理中',
    fixed: '已修复',
    closed: '已关闭',
    待处理: '待处理',
    已确认: '已确认',
    处理中: '处理中',
    已修复: '已修复',
    已关闭: '已关闭'
  }
  return textMap[status] || status || '-'
}

// 解析步骤
const parseSteps = (steps) => {
  if (Array.isArray(steps)) return steps
  if (typeof steps === 'string') {
    try {
      const parsed = JSON.parse(steps)
      if (Array.isArray(parsed)) return parsed
      return steps.split('\n').filter(s => s.trim())
    } catch {
      return steps.split('\n').filter(s => s.trim())
    }
  }
  return []
}

// 判断是否为整合型 Bug 报告（一键测试会话汇总）
const isConsolidatedBug = (bug) => {
  if (!bug) return false
  return bug.bug_name && bug.bug_name.includes('Bug汇总')
}

// 表格列定义
const columns = [
  { title: 'ID', key: 'id', width: 80 },
  { title: 'Bug 标题', key: 'bug_name', width: 250, ellipsis: { tooltip: true } },
  { 
    title: '测试类型', 
    key: 'case_type', 
    width: 110,
    render(row) {
      const type = row.case_type || '功能测试'
      const typeMap = {
        '功能测试': 'info',
        '接口测试': 'warning',
        '单元测试': 'success',
        '性能测试': 'error',
        '安全测试': 'error'
      }
      return h(NTag, { type: typeMap[type] || 'default', size: 'small' }, 
        { default: () => type }
      )
    }
  },
  { 
    title: '执行模式', 
    key: 'execution_mode', 
    width: 90,
    render(row) {
      const mode = row.execution_mode || '单量'
      return h(NTag, { type: mode === '批量' ? 'warning' : 'default', size: 'small' }, 
        { default: () => mode }
      )
    }
  },
  { 
    title: '严重程度', 
    key: 'severity_level', 
    width: 100,
    render(row) {
      return h(NTag, { type: getSeverityType(row.severity_level), size: 'small' }, 
        { default: () => row.severity_level || '-' }
      )
    }
  },
  { 
    title: '状态', 
    key: 'status', 
    width: 90,
    render(row) {
      return h(NTag, { type: getStatusType(row.status), size: 'small' }, 
        { default: () => formatStatus(row.status) }
      )
    }
  },
  { 
    title: '关联用例', 
    key: 'test_case_id', 
    width: 100, 
    render(row) {
      return row.test_case_id ? `用例 #${row.test_case_id}` : '-'
    }
  },
  { title: '发现时间', key: 'created_at', width: 160 },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    fixed: 'right',
    render(row) {
      return h(NSpace, {}, {
        default: () => [
          h(NButton, { size: 'small', type: 'primary', onClick: () => viewDetail(row) }, 
            { default: () => '查看详情' }
          ),
          h(NButton, { 
            size: 'small', 
            type: row.status === 'fixed' ? 'default' : 'success',
            disabled: row.status === 'fixed',
            onClick: () => updateStatus(row, 'fixed')
          }, { 
            default: () => '已修复' 
          }),
          h(NButton, { size: 'small', type: 'error', onClick: () => deleteBug(row) }, 
            { default: () => '删除' }
          )
        ]
      })
    }
  }
]





// 查看详情
const viewDetail = async (row) => {
  detailDialogVisible.value = true
  detailLoading.value = true
  
  try {
    const result = await bugReportAPI.getDetail(row.id)
    if (result.success) {
      currentBug.value = result.data
    } else {
      // 如果没有详细接口，使用行数据
      currentBug.value = row
    }
  } catch (error) {
    // 使用行数据作为后备
    currentBug.value = row
  } finally {
    detailLoading.value = false
  }
}

// 将内部状态编码转换为后端使用的中文状态
const mapStatusForApi = (status) => {
  const map = {
    open: '待处理',
    in_progress: '已确认',
    fixed: '已修复',
    closed: '已关闭'
  }
  return map[status] || status
}

// 更新状态
const updateStatus = async (row, newStatus) => {
  const apiStatus = mapStatusForApi(newStatus)
  try {
    const result = await bugReportAPI.updateStatus(row.id, apiStatus)
    if (result.success) {
      message.success('状态更新成功')
      loadBugs()
    } else {
      message.error(result.message || '更新失败')
    }
  } catch (error) {
    message.error('更新失败')
    console.error(error)
  }
}

// 标记为已修复
const markAsFixed = () => {
  if (!currentBug.value) return
  
  dialog.success({
    title: '确认修复',
    content: '确定要将此 Bug 标记为已修复吗？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      await updateStatus(currentBug.value, 'fixed')
      currentBug.value.status = '已修复'
    }
  })
}

// 删除 Bug
const deleteBug = (row) => {
  dialog.warning({
    title: '确认删除',
    content: `确定要删除 Bug "${row.title}" 吗？`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await bugReportAPI.delete(row.id)
        if (result.success) {
          message.success('删除成功')
          loadBugs()
        } else {
          message.error(result.message || '删除失败')
        }
      } catch (error) {
        message.error('删除失败')
        console.error(error)
      }
    }
  })
}

// 下载 Bug 报告
const downloadBugReport = () => {
  if (!currentBug.value) return
  
  const dataStr = JSON.stringify(currentBug.value, null, 2)
  const blob = new Blob([dataStr], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `bug_report_${currentBug.value.id}.json`
  link.click()
  URL.revokeObjectURL(url)
  
  message.success('报告下载成功')
}


</script>

<style scoped>
.bug-report-view {
  padding: 0;
}

.text-primary {
  color: #007857;
}

.stat-card {
  text-align: center;
}

.stat-critical :deep(.n-statistic-value) {
  color: #f5222d;
}

.stat-open :deep(.n-statistic-value) {
  color: #faad14;
}

.stat-fixed :deep(.n-statistic-value) {
  color: #52c41a;
}

.bug-description {
  padding: 12px;
  background-color: #f5f7fa;
  border-radius: 4px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.bug-steps {
  padding: 12px;
  background-color: #f5f7fa;
  border-radius: 4px;
}

.bug-steps ol {
  margin: 0;
  padding-left: 20px;
}

.bug-steps li {
  margin: 8px 0;
  line-height: 1.6;
}

.error-message {
  margin: 0;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.json-output-wrapper {
  background-color: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
}
</style>
