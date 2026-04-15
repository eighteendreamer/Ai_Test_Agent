<template>
  <div class="provider-manage-view">
    <!-- 页面标题 -->
    <div class="mb-6">
      <div class="flex items-center gap-3 mb-2">
        <div class="w-10 h-10 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
          <i class="fas fa-building text-white text-lg"></i>
        </div>
        <div>
          <h1 class="text-2xl font-bold text-slate-800">供应商管理</h1>
          <p class="text-sm text-slate-500">管理 LLM 模型供应商信息，配置默认 API 地址</p>
        </div>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="grid grid-cols-3 gap-5 mb-6">
      <div class="stat-card-modern bg-gradient-to-br from-indigo-50 to-blue-50 border border-indigo-100">
        <div class="flex items-start justify-between">
          <div>
            <div class="flex items-center gap-2 mb-1">
              <div class="w-8 h-8 rounded-lg bg-indigo-500 flex items-center justify-center">
                <i class="fas fa-building text-white text-sm"></i>
              </div>
              <span class="text-sm font-medium text-slate-600">供应商总数</span>
            </div>
            <p class="text-2xl font-bold text-slate-800 mt-2">{{ providers.length }}</p>
          </div>
        </div>
      </div>
      <div class="stat-card-modern bg-gradient-to-br from-green-50 to-emerald-50 border border-green-100">
        <div class="flex items-start justify-between">
          <div>
            <div class="flex items-center gap-2 mb-1">
              <div class="w-8 h-8 rounded-lg bg-green-500 flex items-center justify-center">
                <i class="fas fa-check-circle text-white text-sm"></i>
              </div>
              <span class="text-sm font-medium text-slate-600">已启用</span>
            </div>
            <p class="text-2xl font-bold text-slate-800 mt-2">{{ activeCount }}</p>
          </div>
        </div>
      </div>
      <div class="stat-card-modern bg-gradient-to-br from-purple-50 to-violet-50 border border-purple-100">
        <div class="flex items-start justify-between">
          <div>
            <div class="flex items-center gap-2 mb-1">
              <div class="w-8 h-8 rounded-lg bg-purple-500 flex items-center justify-center">
                <i class="fas fa-robot text-white text-sm"></i>
              </div>
              <span class="text-sm font-medium text-slate-600">关联模型数</span>
            </div>
            <p class="text-2xl font-bold text-slate-800 mt-2">{{ totalModelCount }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 供应商列表 -->
    <div class="bg-white rounded-xl shadow-sm border border-slate-200">
      <div class="p-5 border-b border-slate-200 flex items-center justify-between">
        <div>
          <h2 class="text-lg font-bold text-slate-800">供应商列表</h2>
          <p class="text-sm text-slate-500 mt-1">管理模型供应商的基本信息和默认配置</p>
        </div>
        <n-button type="primary" @click="openAddModal">
          <template #icon><i class="fas fa-plus"></i></template>
          添加供应商
        </n-button>
      </div>

      <n-spin :show="loading">
        <div class="p-5">
          <div v-if="providers.length === 0" class="text-center py-12">
            <i class="fas fa-inbox text-slate-300 text-5xl mb-4"></i>
            <p class="text-slate-500">暂无供应商配置</p>
          </div>

          <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
            <div
              v-for="provider in providers"
              :key="provider.id"
              class="provider-card group"
              :class="{ 'disabled': provider.is_active !== 1 }"
            >
              <!-- 顶部：图标 + 开关 -->
              <div class="flex items-start justify-between mb-4">
                <div class="w-14 h-14 rounded-2xl flex items-center justify-center transition-colors duration-300"
                  :class="provider.is_active === 1 ? 'bg-indigo-50 text-indigo-600' : 'bg-slate-100 text-slate-400'">
                  <i class="fas fa-cloud text-2xl"></i>
                </div>
                <div class="flex items-center gap-2">
                  <n-tag size="small" :type="provider.is_active === 1 ? 'success' : 'default'" round :bordered="false" class="px-2">
                    {{ provider.is_active === 1 ? '已启用' : '已禁用' }}
                  </n-tag>
                  <n-switch
                    :value="provider.is_active === 1"
                    @update:value="toggleProvider(provider)"
                    size="small"
                    class="ml-1"
                  />
                </div>
              </div>

              <!-- 中部：信息 -->
              <div class="mb-5">
                <div class="flex items-center gap-2 mb-2">
                  <h3 class="text-lg font-bold text-slate-800 truncate" :title="provider.display_name">
                    {{ provider.display_name }}
                  </h3>
                  <span class="px-2 py-0.5 bg-slate-100 text-slate-500 text-xs rounded-md font-mono border border-slate-200">
                    {{ provider.code }}
                  </span>
                </div>
                
                <div class="space-y-2">
                  <div class="flex items-center text-xs text-slate-500" title="API Base URL">
                    <i class="fas fa-link w-4 text-center mr-1.5 text-slate-400"></i>
                    <span v-if="provider.default_base_url" class="truncate font-mono bg-slate-50 px-1.5 py-0.5 rounded text-slate-600">
                      {{ provider.default_base_url }}
                    </span>
                    <span v-else class="text-slate-400 italic">未设置默认 URL</span>
                  </div>
                  
                  <div class="flex items-start text-xs text-slate-500 min-h-[2.5em]">
                    <i class="fas fa-info-circle w-4 text-center mr-1.5 mt-0.5 text-slate-400"></i>
                    <p class="line-clamp-2 leading-relaxed">
                      {{ provider.description || '暂无描述信息' }}
                    </p>
                  </div>
                </div>
              </div>

              <!-- 底部：统计 + 操作 -->
              <div class="flex items-center justify-between pt-4 border-t border-slate-100">
                <div class="flex items-center gap-3">
                  <div class="flex items-center text-xs text-slate-500" title="关联模型数量">
                    <i class="fas fa-cubes mr-1.5 text-indigo-400"></i>
                    <span class="font-medium text-slate-700">{{ provider.model_count }}</span>
                    <span class="ml-1 text-slate-400">模型</span>
                  </div>
                  <div class="flex items-center text-xs text-slate-400" title="排序权重">
                    <i class="fas fa-sort-amount-down mr-1"></i>
                    {{ provider.sort_order }}
                  </div>
                </div>

                <div class="flex items-center gap-2 opacity-80 group-hover:opacity-100 transition-opacity">
                  <n-button size="tiny" secondary circle @click="editProvider(provider)" title="编辑配置">
                    <template #icon><i class="fas fa-pen text-xs"></i></template>
                  </n-button>
                  <n-button
                    size="tiny"
                    secondary
                    circle
                    type="error"
                    @click="deleteProvider(provider)"
                    :disabled="provider.model_count > 0"
                    title="删除配置"
                  >
                    <template #icon><i class="fas fa-trash text-xs"></i></template>
                  </n-button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </n-spin>
    </div>

    <!-- 添加/编辑供应商对话框 -->
    <n-modal
      v-model:show="modalVisible"
      preset="card"
      :title="isEditing ? '编辑供应商' : '添加供应商'"
      style="width: 560px"
    >
      <n-form
        ref="formRef"
        :model="formData"
        :rules="formRules"
        label-placement="left"
        label-width="120"
      >
        <n-form-item label="供应商名称" path="name">
          <n-input v-model:value="formData.name" placeholder="如：OpenAI, DeepSeek" />
        </n-form-item>
        <n-form-item label="供应商代码" path="code">
          <n-input v-model:value="formData.code" placeholder="如：openai, deepseek（唯一标识）" :disabled="isEditing" />
        </n-form-item>
        <n-form-item label="显示名称" path="display_name">
          <n-input v-model:value="formData.display_name" placeholder="如：OpenAI, DeepSeek (深度求索)" />
        </n-form-item>
        <n-form-item label="默认API地址" path="default_base_url">
          <n-input v-model:value="formData.default_base_url" placeholder="如：https://api.openai.com/v1" />
        </n-form-item>
        <n-form-item label="排序" path="sort_order">
          <n-input-number v-model:value="formData.sort_order" :min="0" :max="999" placeholder="数字越小越靠前" />
        </n-form-item>
        <n-form-item label="备注" path="description">
          <n-input v-model:value="formData.description" type="textarea" placeholder="供应商备注信息（可选）" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="modalVisible = false">取消</n-button>
          <n-button type="primary" @click="submitForm" :loading="submitting">
            {{ isEditing ? '保存' : '添加' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import {
  NButton, NSpace, NTag, NSpin, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSwitch,
  useMessage, useDialog
} from 'naive-ui'
import { modelAPI } from '@/api'

const message = useMessage()
const dialog = useDialog()

const providers = ref([])
const loading = ref(false)
const modalVisible = ref(false)
const isEditing = ref(false)
const submitting = ref(false)
const formRef = ref(null)

const activeCount = computed(() => providers.value.filter(p => p.is_active === 1).length)
const totalModelCount = computed(() => providers.value.reduce((sum, p) => sum + (p.model_count || 0), 0))

const formData = reactive({
  id: null,
  name: '',
  code: '',
  display_name: '',
  default_base_url: '',
  sort_order: 0,
  description: ''
})

const formRules = {
  name: { required: true, message: '请输入供应商名称', trigger: 'blur' },
  code: { required: true, message: '请输入供应商代码', trigger: 'blur' },
  display_name: { required: true, message: '请输入显示名称', trigger: 'blur' }
}

const loadProviders = async () => {
  loading.value = true
  try {
    const result = await modelAPI.getAllProviders()
    if (result.success && Array.isArray(result.data)) {
      providers.value = result.data
    }
  } catch (error) {
    message.error('获取供应商列表失败')
  } finally {
    loading.value = false
  }
}

const openAddModal = () => {
  isEditing.value = false
  Object.assign(formData, {
    id: null, name: '', code: '', display_name: '',
    default_base_url: '', sort_order: 0, description: ''
  })
  modalVisible.value = true
}

const editProvider = (provider) => {
  isEditing.value = true
  Object.assign(formData, {
    id: provider.id,
    name: provider.name,
    code: provider.code,
    display_name: provider.display_name,
    default_base_url: provider.default_base_url || '',
    sort_order: provider.sort_order || 0,
    description: provider.description || ''
  })
  modalVisible.value = true
}

const submitForm = async () => {
  try { await formRef.value?.validate() } catch { return }
  submitting.value = true
  try {
    const result = isEditing.value
      ? await modelAPI.updateProvider(formData.id, formData)
      : await modelAPI.createProvider(formData)
    if (result.success) {
      message.success(isEditing.value ? '更新成功' : '添加成功')
      modalVisible.value = false
      loadProviders()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (error) {
    const detail = error.response?.data?.detail
    message.error(detail || '操作失败: ' + (error.message || ''))
  } finally {
    submitting.value = false
  }
}

const toggleProvider = async (provider) => {
  try {
    const result = await modelAPI.toggleProvider(provider.id)
    if (result.success) {
      message.success(result.message)
      loadProviders()
    }
  } catch (error) {
    message.error('操作失败')
  }
}

const deleteProvider = (provider) => {
  if (provider.model_count > 0) {
    message.warning(`该供应商下有 ${provider.model_count} 个模型，无法删除`)
    return
  }
  dialog.warning({
    title: '确认删除',
    content: `确定要删除供应商 "${provider.display_name}" 吗？`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await modelAPI.deleteProvider(provider.id)
        if (result.success) {
          message.success('删除成功')
          loadProviders()
        }
      } catch (error) {
        const detail = error.response?.data?.detail
        message.error(detail || '删除失败')
      }
    }
  })
}

onMounted(() => {
  loadProviders()
})
</script>

<style scoped>
.provider-manage-view { padding: 0; }

.stat-card-modern {
  padding: 1.25rem;
  border-radius: 1rem;
  transition: all 0.3s ease;
}
.stat-card-modern:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.1);
}

.provider-card {
  padding: 1.25rem 1.5rem;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 1rem;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}
.provider-card:hover {
  border-color: #007857;
  box-shadow: 0 8px 24px rgba(0, 120, 87, 0.12);
  transform: translateY(-2px);
}
.provider-card.disabled {
  opacity: 0.6;
  background: #f9fafb;
}
.provider-card.disabled:hover {
  border-color: #d1d5db;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
}
</style>
