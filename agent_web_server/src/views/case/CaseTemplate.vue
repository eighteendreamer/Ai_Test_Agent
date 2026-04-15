<template>
  <div class="case-template-view">

    <!-- 当前模板状态卡片 -->
    <n-card class="mb-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="status-dot" :class="activeTemplate.source_platform ? 'active' : 'default'" />
          <div>
            <div class="font-semibold text-slate-700">
              {{ activeTemplate.template_name || '系统默认模板' }}
            </div>
            <div class="text-xs text-slate-400 mt-0.5">
              <span v-if="activeTemplate.source_platform">
                来源：{{ activeTemplate.source_platform }} ·
                上次同步：{{ formatDate(activeTemplate.synced_at) }}
              </span>
              <span v-else>使用系统内置字段结构，未绑定任何平台</span>
            </div>
          </div>
        </div>
        <div class="flex gap-2">
          <n-button size="small" @click="showResetConfirm = true" :disabled="!activeTemplate.id">
            重置为默认
          </n-button>
        </div>
      </div>
    </n-card>

    <n-grid :cols="2" :x-gap="16">
      <!-- 左：从平台同步 -->
      <n-gi>
        <n-card title="从项目管理平台同步模板" class="h-full">
          <p class="text-sm text-slate-500 mb-4">
            选择已配置的项目管理平台，一键同步其用例字段结构作为全局生成模板。
          </p>
          <n-form-item label="选择平台">
            <n-select
              v-model:value="selectedPlatform"
              :options="platformOptions"
              placeholder="请选择平台"
              clearable
            />
          </n-form-item>
          <n-button
            type="primary"
            :loading="syncing"
            :disabled="!selectedPlatform"
            @click="handleSync"
            block
          >
            <template #icon><i class="fas fa-sync-alt" /></template>
            同步用例模板
          </n-button>

          <!-- 已保存的模板列表 -->
          <div v-if="savedTemplates.length" class="mt-4">
            <div class="text-xs text-slate-400 mb-2">已保存的模板</div>
            <div
              v-for="tpl in savedTemplates"
              :key="tpl.id"
              class="tpl-item"
              :class="{ 'tpl-item--active': tpl.is_active }"
              @click="activateTemplate(tpl)"
            >
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium">{{ tpl.template_name }}</span>
                <n-tag size="small" :type="tpl.is_active ? 'success' : 'default'">
                  {{ tpl.is_active ? '使用中' : '未启用' }}
                </n-tag>
              </div>
              <div class="text-xs text-slate-400 mt-0.5">
                {{ tpl.fields?.length || 0 }} 个字段 · 同步于 {{ formatDate(tpl.synced_at) }}
              </div>
            </div>
          </div>
        </n-card>
      </n-gi>

      <!-- 右：字段预览与编辑 -->
      <n-gi>
        <n-card title="当前模板字段" class="h-full">
          <template #header-extra>
            <n-button size="small" type="primary" ghost @click="saveEdit" :loading="saving" v-if="edited">
              保存修改
            </n-button>
          </template>

          <div v-if="!fields.length" class="text-slate-400 text-sm text-center py-8">
            暂无字段，请先从平台同步或使用系统默认
          </div>

          <draggable
            v-else
            v-model="fields"
            item-key="key"
            handle=".drag-handle"
            @change="edited = true"
          >
            <template #item="{ element: field, index }">
              <div class="field-row">
                <i class="fas fa-grip-vertical drag-handle text-slate-300 cursor-grab mr-2" />
                <div class="flex-1">
                  <div class="flex items-center gap-2">
                    <n-input
                      v-model:value="field.label"
                      size="small"
                      style="width: 120px"
                      @update:value="edited = true"
                    />
                    <n-tag size="small" :type="field.required ? 'error' : 'default'">
                      {{ field.required ? '必填' : '可选' }}
                    </n-tag>
                    <n-tag size="small">{{ field.type }}</n-tag>
                  </div>
                  <div class="text-xs text-slate-400 mt-0.5">字段键：{{ field.key }}</div>
                </div>
                <n-switch
                  :value="field.required"
                  size="small"
                  @update:value="val => { field.required = val; edited = true }"
                />
              </div>
            </template>
          </draggable>

          <!-- 附加提示词 -->
          <div class="mt-4">
            <div class="text-sm text-slate-600 mb-1">附加 LLM 提示词（可选）</div>
            <n-input
              v-model:value="extraPrompt"
              type="textarea"
              :rows="3"
              placeholder="例如：生成的用例步骤需要包含具体的测试数据，优先级使用数字1-4..."
              @update:value="edited = true"
            />
          </div>

          <!-- 选项预览 -->
          <n-collapse class="mt-4" arrow-placement="right">
            <n-collapse-item title="优先级选项" name="priority">
              <div class="flex flex-wrap gap-2">
                <n-tag v-for="o in activeTemplate.priority_options" :key="o.value" size="small">
                  {{ o.value }} - {{ o.label }}
                </n-tag>
              </div>
            </n-collapse-item>
            <n-collapse-item title="用例类型选项" name="case_type">
              <div class="flex flex-wrap gap-2">
                <n-tag v-for="o in activeTemplate.case_type_options" :key="o.value" size="small">
                  {{ o.value }}
                </n-tag>
              </div>
            </n-collapse-item>
            <n-collapse-item title="适用阶段选项" name="stage">
              <div class="flex flex-wrap gap-2">
                <n-tag v-for="o in activeTemplate.stage_options" :key="o.value" size="small">
                  {{ o.value }}
                </n-tag>
              </div>
            </n-collapse-item>
          </n-collapse>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 重置确认弹窗 -->
    <n-modal v-model:show="showResetConfirm" preset="dialog" type="warning"
      title="重置为系统默认模板"
      content="将停用所有自定义模板，用例生成将使用系统内置字段结构。确认重置？"
      positive-text="确认重置"
      negative-text="取消"
      @positive-click="handleReset"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import {
  NCard, NGrid, NGi, NButton, NSelect, NFormItem, NInput, NTag,
  NSwitch, NCollapse, NCollapseItem, NModal, useMessage
} from 'naive-ui'
import draggable from 'vuedraggable'
import {
  getActiveTemplate, listTemplates, syncFromPlatform,
  updateTemplate, resetToDefault
} from '@/api/caseTemplate'
import { listActivePlatforms } from '@/api/project'

const message = useMessage()

const activeTemplate = ref({ template_name: '系统默认模板', fields: [], source_platform: null })
const savedTemplates = ref([])
const platformOptions = ref([])
const selectedPlatform = ref(null)
const syncing = ref(false)
const saving = ref(false)
const edited = ref(false)
const showResetConfirm = ref(false)

// 可编辑的字段列表和附加提示词（从 activeTemplate 派生）
const fields = ref([])
const extraPrompt = ref('')

const formatDate = (iso) => {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const loadData = async () => {
  const [tplRes, listRes, platformRes] = await Promise.all([
    getActiveTemplate(),
    listTemplates(),
    listActivePlatforms(),
  ])
  if (tplRes.success) {
    activeTemplate.value = tplRes.data
    fields.value = JSON.parse(JSON.stringify(tplRes.data.fields || []))
    extraPrompt.value = tplRes.data.extra_prompt || ''
  }
  if (listRes.success) savedTemplates.value = listRes.data
  if (platformRes.success) {
    platformOptions.value = platformRes.data.map(p => ({
      label: p.platform_name,
      value: p.platform_id,
    }))
  }
  edited.value = false
}

const handleSync = async () => {
  if (!selectedPlatform.value) return
  syncing.value = true
  try {
    const res = await syncFromPlatform(selectedPlatform.value)
    if (res.success) {
      message.success(res.message)
      await loadData()
    } else {
      message.error(res.message || '同步失败')
    }
  } catch {
    message.error('同步失败，请检查平台配置')
  } finally {
    syncing.value = false
  }
}

const activateTemplate = async (tpl) => {
  if (tpl.is_active) return
  try {
    await updateTemplate(tpl.id, { is_active: 1 })
    message.success(`已切换到：${tpl.template_name}`)
    await loadData()
  } catch {
    message.error('切换失败')
  }
}

const saveEdit = async () => {
  if (!activeTemplate.value.id) {
    message.warning('系统默认模板无法直接编辑，请先从平台同步一个模板')
    return
  }
  saving.value = true
  try {
    await updateTemplate(activeTemplate.value.id, {
      fields: fields.value,
      extra_prompt: extraPrompt.value,
    })
    message.success('模板已保存')
    edited.value = false
    await loadData()
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

const handleReset = async () => {
  try {
    const res = await resetToDefault()
    if (res.success) {
      message.success(res.message)
      await loadData()
    }
  } catch {
    message.error('重置失败')
  }
}

onMounted(loadData)
</script>

<style scoped>
.case-template-view { padding: 0; }

.status-dot {
  width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
}
.status-dot.active  { background: #18a058; box-shadow: 0 0 0 3px #18a05820; }
.status-dot.default { background: #94a3b8; }

.tpl-item {
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: border-color .2s, background .2s;
}
.tpl-item:hover { border-color: #007857; background: #f0fdf4; }
.tpl-item--active { border-color: #18a058; background: #f0fdf4; }

.field-row {
  display: flex;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #f1f5f9;
}
.field-row:last-child { border-bottom: none; }
</style>
