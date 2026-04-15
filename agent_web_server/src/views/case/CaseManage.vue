<template>
  <div class="case-manage-view">
    <!-- 操作栏 -->
    <n-card>
      <n-form inline :model="filters" label-placement="left">
        <n-form-item label="模块">
          <n-input v-model:value="filters.module" placeholder="筛选模块" clearable style="width: 150px" />
        </n-form-item>
        <n-form-item label="关键词">
          <n-input v-model:value="filters.search" placeholder="搜索用例名称" clearable style="width: 200px" />
        </n-form-item>
        <n-form-item label="优先级">
          <n-select v-model:value="filters.priority" :options="filterPriorityOptions" clearable placeholder="全部" style="width: 100px" />
        </n-form-item>
      </n-form>
    </n-card>

    <!-- 用例列表 -->
    <n-card style="margin-top: 20px">
      <template #header>
        <div class="flex items-center gap-2">
          <span class="font-bold">测试用例列表</span>
          <!-- 模板来源提示 -->
          <n-tag v-if="templateSource" size="small" type="success">
            <template #icon><i class="fas fa-layer-group" /></template>
            模板：{{ templateSource }}
          </n-tag>
        </div>
      </template>
      <template #header-extra>
        <n-button type="primary" @click="openCreateDialog">
          <template #icon><i class="fas fa-plus"></i></template>
          新建用例
        </n-button>
      </template>

      <n-data-table :columns="columns" :data="testCases" :loading="loading" :row-key="row => row.id" striped />

      <div style="margin-top: 20px; display: flex; justify-content: flex-end;">
        <n-pagination
          v-model:page="currentPage"
          v-model:page-size="pageSize"
          :item-count="total"
          :page-sizes="[10, 20, 50]"
          show-size-picker
          @update:page="goToPage"
          @update:page-size="changePageSize"
        />
      </div>
    </n-card>

    <!-- 详情对话框 -->
    <n-modal v-model:show="detailDialogVisible" preset="card" title="测试用例详情" style="width: 800px">
      <n-descriptions v-if="currentCase" :column="1" label-placement="left" bordered>
        <n-descriptions-item label="ID">{{ currentCase.id }}</n-descriptions-item>
        <!-- 按模板字段顺序渲染 -->
        <template v-for="field in templateFields" :key="field.key">
          <n-descriptions-item :label="field.label">
            <!-- 步骤特殊渲染 -->
            <template v-if="field.key === 'steps'">
              <div
                v-for="(step, index) in parseSteps(currentCase.steps)"
                :key="index"
                style="margin-bottom: 8px; padding: 6px 8px; background: #f8f8f8; border-radius: 4px;"
              >
                <div><strong>步骤 {{ index + 1 }}：</strong>{{ step.step }}</div>
                <div v-if="step.expected" style="color: #666; margin-top: 2px;">
                  <strong>预期：</strong>{{ step.expected }}
                </div>
              </div>
            </template>
            <!-- 优先级特殊渲染 -->
            <template v-else-if="field.key === 'priority'">
              <n-tag :type="getPriorityType(currentCase.priority)" size="small">
                {{ formatPriorityLabel(currentCase.priority) }}
              </n-tag>
            </template>
            <!-- 普通字段 -->
            <template v-else>
              {{ currentCase[field.key] || '—' }}
            </template>
          </n-descriptions-item>
        </template>
      </n-descriptions>
    </n-modal>

    <!-- 新建/编辑对话框 -->
    <n-modal
      v-model:show="editDialogVisible"
      preset="card"
      :title="isEditing ? '编辑用例' : '新建用例'"
      style="width: 800px"
    >
      <n-form ref="formRef" :model="formData" :rules="formRules" label-placement="left" label-width="100">
        <!-- 按模板字段顺序动态渲染 -->
        <template v-for="field in templateFields" :key="field.key">

          <!-- 步骤字段 -->
          <n-form-item v-if="field.key === 'steps'" :label="field.label" path="steps">
            <div style="width: 100%">
              <div v-for="(step, index) in formData.steps" :key="index" class="step-item">
                <n-input v-model:value="formData.steps[index]" :placeholder="`步骤 ${index + 1}`" />
                <n-button v-if="formData.steps.length > 1" type="error" text @click="removeStep(index)">
                  <i class="fas fa-times"></i>
                </n-button>
              </div>
              <n-button dashed block @click="addStep" style="margin-top: 8px">
                <template #icon><i class="fas fa-plus"></i></template>
                添加步骤
              </n-button>
            </div>
          </n-form-item>

          <!-- 优先级 select -->
          <n-form-item v-else-if="field.key === 'priority'" :label="field.label" path="priority">
            <n-select
              v-model:value="formData.priority"
              :options="prioritySelectOptions"
              :placeholder="`请选择${field.label}`"
            />
          </n-form-item>

          <!-- 用例类型 select -->
          <n-form-item v-else-if="field.key === 'case_type'" :label="field.label" path="case_type">
            <n-select
              v-model:value="formData.case_type"
              :options="caseTypeSelectOptions"
              :placeholder="`请选择${field.label}`"
            />
          </n-form-item>

          <!-- 适用阶段 select -->
          <n-form-item v-else-if="field.key === 'stage'" :label="field.label" path="stage">
            <n-select
              v-model:value="formData.stage"
              :options="stageSelectOptions"
              :placeholder="`请选择${field.label}`"
            />
          </n-form-item>

          <!-- textarea 字段 -->
          <n-form-item
            v-else-if="field.type === 'textarea'"
            :label="field.label"
            :path="field.key"
          >
            <n-input
              v-model:value="formData[field.key]"
              type="textarea"
              :placeholder="`请输入${field.label}${field.required ? '' : '（可选）'}`"
              :rows="2"
            />
          </n-form-item>

          <!-- 普通 text 字段 -->
          <n-form-item v-else :label="field.label" :path="field.key">
            <n-input
              v-model:value="formData[field.key]"
              :placeholder="`请输入${field.label}${field.required ? '' : '（可选）'}`"
            />
          </n-form-item>

        </template>
      </n-form>

      <template #footer>
        <n-space justify="end">
          <n-button @click="editDialogVisible = false">取消</n-button>
          <n-button type="primary" @click="submitForm" :loading="submitting">
            {{ isEditing ? '保存' : '创建' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, h, reactive, onMounted, computed } from 'vue'
import {
  NCard, NButton, NDataTable, NModal, NDescriptions, NDescriptionsItem,
  NTag, NForm, NFormItem, NInput, NSelect, NSpace, NPagination,
  useMessage, useDialog
} from 'naive-ui'
import { testCaseAPI } from '@/api'
import { useLazyLoad } from '@/composables/useLazyLoad'
import { parseSteps, stepsToEditArray, editArrayToJson } from '@/utils/stepsUtils'
import { getActiveTemplate } from '@/api/caseTemplate'

const message = useMessage()
const dialog = useDialog()

// ── 模板相关 ──────────────────────────────────────────────────────────
const templateFields = ref([
  { key: 'module',       label: '所属模块',   required: true,  type: 'text' },
  { key: 'title',        label: '用例标题',   required: true,  type: 'text' },
  { key: 'precondition', label: '前置条件',   required: false, type: 'textarea' },
  { key: 'steps',        label: '测试步骤',   required: true,  type: 'steps' },
  { key: 'expected',     label: '预期结果',   required: true,  type: 'textarea' },
  { key: 'keywords',     label: '关键词',     required: false, type: 'text' },
  { key: 'priority',     label: '优先级',     required: true,  type: 'select' },
  { key: 'case_type',    label: '用例类型',   required: false, type: 'select' },
  { key: 'stage',        label: '适用阶段',   required: false, type: 'select' },
])
const templateSource = ref('')
const prioritySelectOptions = ref([
  { label: '1级 - 最高', value: '1' },
  { label: '2级 - 高',   value: '2' },
  { label: '3级 - 中',   value: '3' },
  { label: '4级 - 低',   value: '4' },
])
const caseTypeSelectOptions = ref([
  { label: '功能测试', value: '功能测试' },
  { label: '冒烟测试', value: '冒烟测试' },
  { label: '回归测试', value: '回归测试' },
  { label: '边界测试', value: '边界测试' },
  { label: '异常测试', value: '异常测试' },
])
const stageSelectOptions = ref([
  { label: '开发阶段', value: '开发阶段' },
  { label: '测试阶段', value: '测试阶段' },
  { label: '验收阶段', value: '验收阶段' },
  { label: '上线阶段', value: '上线阶段' },
])

// 优先级标签映射（用于详情展示）
const priorityLabelMap = ref({})

const loadTemplate = async () => {
  try {
    const res = await getActiveTemplate()
    if (!res.success) return
    const tpl = res.data
    if (tpl.fields?.length) templateFields.value = tpl.fields
    // 无论是否有 source_platform，都显示模板名称
    templateSource.value = tpl.template_name || tpl.source_platform || '系统默认模板'
    if (tpl.priority_options?.length) {
      prioritySelectOptions.value = tpl.priority_options.map(o => ({ label: `${o.value} - ${o.label}`, value: String(o.value) }))
      priorityLabelMap.value = Object.fromEntries(tpl.priority_options.map(o => [String(o.value), o.label]))
    }
    if (tpl.case_type_options?.length)
      caseTypeSelectOptions.value = tpl.case_type_options.map(o => ({ label: o.label, value: o.value }))
    if (tpl.stage_options?.length)
      stageSelectOptions.value = tpl.stage_options.map(o => ({ label: o.label, value: o.value }))
  } catch (e) {
    console.warn('[CaseManage] 加载模板失败，使用默认选项', e)
  }
}

// 筛选用优先级选项（带"全部"）
const filterPriorityOptions = computed(() => [
  { label: '全部', value: null },
  ...prioritySelectOptions.value.map(o => ({ label: o.label.split(' - ')[0], value: o.value }))
])

// ── 筛选 ──────────────────────────────────────────────────────────────
const filters = reactive({
  module: '',
  search: '',
  priority: null,
  project_id: parseInt(localStorage.getItem('currentProjectId')) || null
})

const {
  data: testCases, loading, currentPage, pageSize, total,
  refresh, goToPage, changePageSize
} = useLazyLoad({
  fetchFunction: testCaseAPI.getList,
  pageSize: 10,
  filters,
  autoLoad: true,
  debounceDelay: 500
})

// ── 详情 ──────────────────────────────────────────────────────────────
const detailDialogVisible = ref(false)
const currentCase = ref(null)

const getPriorityType = (priority) => {
  const map = { '1': 'error', '2': 'warning', '3': 'info', '4': 'default' }
  return map[String(priority)] || 'default'
}

const formatPriorityLabel = (priority) => {
  const p = String(priority)
  return priorityLabelMap.value[p] ? `${p} - ${priorityLabelMap.value[p]}` : `${p}级`
}

// ── 编辑表单 ──────────────────────────────────────────────────────────
const editDialogVisible = ref(false)
const isEditing = ref(false)
const submitting = ref(false)
const formRef = ref(null)

// 动态 formData：包含所有可能的字段键
const formData = reactive({
  id: null,
  module: '', title: '', precondition: '', steps: [''],
  expected: '', keywords: '', priority: '2',
  case_type: '功能测试', stage: '测试阶段',
})

const formRules = computed(() => {
  const rules = {}
  templateFields.value.forEach(f => {
    if (f.required && f.key !== 'steps') {
      rules[f.key] = { required: true, message: `请输入${f.label}`, trigger: 'blur' }
    }
  })
  rules.steps = {
    required: true,
    validator: (_, value) => {
      if (!value || value.every(s => !s.trim())) return new Error('请至少输入一个测试步骤')
      return true
    },
    trigger: 'blur'
  }
  rules.priority = { required: true, message: '请选择优先级', trigger: 'change' }
  return rules
})

const resetFormData = () => {
  Object.assign(formData, {
    id: null, module: '', title: '', precondition: '', steps: [''],
    expected: '', keywords: '',
    priority: prioritySelectOptions.value[1]?.value || '2',
    case_type: caseTypeSelectOptions.value[0]?.value || '功能测试',
    stage: stageSelectOptions.value[0]?.value || '测试阶段',
  })
}

const openCreateDialog = () => {
  isEditing.value = false
  resetFormData()
  editDialogVisible.value = true
}

const editCase = (row) => {
  isEditing.value = true
  Object.assign(formData, {
    id: row.id,
    module: row.module || '',
    title: row.title || '',
    precondition: row.precondition || '',
    steps: stepsToEditArray(row.steps),
    expected: row.expected || '',
    keywords: row.keywords || '',
    priority: String(row.priority || '2'),
    case_type: row.case_type || caseTypeSelectOptions.value[0]?.value || '功能测试',
    stage: row.stage || stageSelectOptions.value[0]?.value || '测试阶段',
  })
  editDialogVisible.value = true
}

const addStep = () => formData.steps.push('')
const removeStep = (index) => formData.steps.splice(index, 1)

const submitForm = async () => {
  try { await formRef.value?.validate() } catch { return }
  submitting.value = true
  try {
    const data = { ...formData, steps: editArrayToJson(formData.steps) }
    const result = isEditing.value
      ? await testCaseAPI.update(formData.id, data)
      : await testCaseAPI.create(data)
    if (result.success) {
      message.success(isEditing.value ? '更新成功' : '创建成功')
      editDialogVisible.value = false
      refresh()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (e) {
    message.error('操作失败: ' + (e.message || '未知错误'))
  } finally {
    submitting.value = false
  }
}

const deleteCase = (row) => {
  dialog.warning({
    title: '确认删除',
    content: `确定要删除用例 "${row.title}" 吗？`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await testCaseAPI.delete(row.id)
        if (result.success) { message.success('删除成功'); refresh() }
        else message.error(result.message || '删除失败')
      } catch (e) { message.error('删除失败: ' + e.message) }
    }
  })
}

const viewDetail = (row) => { currentCase.value = row; detailDialogVisible.value = true }

// 表格列
const columns = [
  { title: 'ID', key: 'id', width: 80 },
  { title: '模块', key: 'module', width: 120 },
  { title: '用例名称', key: 'title', width: 200, ellipsis: { tooltip: true } },
  {
    title: '步骤', key: 'steps',
    render(row) {
      const steps = parseSteps(row.steps)
      if (!steps.length) return '-'
      return h('div', {}, steps.slice(0, 3).map((s, i) =>
        h('div', { style: 'margin: 2px 0; font-size: 12px;' }, `${i + 1}. ${s.step}`)
      ).concat(steps.length > 3 ? [h('div', { style: 'font-size: 12px; color: #999;' }, `...共${steps.length}步`)] : []))
    }
  },
  {
    title: '优先级', key: 'priority', width: 100,
    render(row) {
      return h(NTag, { type: getPriorityType(row.priority), size: 'small' },
        { default: () => formatPriorityLabel(row.priority) })
    }
  },
  {
    title: '用例类型', key: 'case_type', width: 120,
    render(row) {
      if (!row.case_type) return '-'
      return h(NTag, { type: 'default', size: 'small' },
        { default: () => row.case_type })
    }
  },
  {
    title: '操作', key: 'actions', width: 220, fixed: 'right',
    render(row) {
      return h(NSpace, {}, {
        default: () => [
          h(NButton, { size: 'small', type: 'info',    onClick: () => viewDetail(row) }, { default: () => '详情' }),
          h(NButton, { size: 'small', type: 'primary', onClick: () => editCase(row)   }, { default: () => '编辑' }),
          h(NButton, { size: 'small', type: 'error',   onClick: () => deleteCase(row) }, { default: () => '删除' }),
        ]
      })
    }
  }
]

onMounted(loadTemplate)
</script>

<style scoped>
.case-manage-view { padding: 0; }
.text-primary { color: #007857; }
.step-item { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.step-item .n-input { flex: 1; }
</style>
