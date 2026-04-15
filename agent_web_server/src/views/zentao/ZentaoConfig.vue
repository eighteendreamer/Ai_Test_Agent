<template>
  <div class="zentao-config-container">
    <n-card title="🔗 禅道系统配置" :bordered="false">
      <template #header-extra>
        <n-space>
          <n-button type="info" :loading="testing" @click="handleTestConnection">
            <template #icon><i class="fas fa-plug"></i></template>
            测试连接
          </n-button>
          <n-button type="primary" @click="showCreateModal = true">
            <template #icon><i class="fas fa-plus"></i></template>
            新增配置
          </n-button>
        </n-space>
      </template>

      <!-- 当前激活配置 -->
      <div v-if="activeConfig" class="active-config mb-6">
        <div class="active-badge">
          <i class="fas fa-check-circle"></i> 当前激活配置
        </div>
        <div class="config-details">
          <div class="detail-item">
            <span class="label">配置名称：</span>
            <span class="value">{{ activeConfig.config_name }}</span>
          </div>
          <div class="detail-item">
            <span class="label">禅道地址：</span>
            <span class="value">{{ activeConfig.base_url }}</span>
          </div>
          <div class="detail-item">
            <span class="label">登录账号：</span>
            <span class="value">{{ activeConfig.account }}</span>
          </div>
          <div class="detail-item">
            <span class="label">默认产品ID：</span>
            <span class="value">{{ activeConfig.default_product_id || '未设置' }}</span>
          </div>
          <div class="detail-item">
            <span class="label">API版本：</span>
            <n-tag type="info" size="small">{{ activeConfig.api_version }}</n-tag>
          </div>
        </div>
      </div>
      <div v-else class="no-active-tip">
        <n-empty description="暂无激活的禅道配置，请先创建并激活">
          <template #extra>
            <n-button type="primary" size="small" @click="showCreateModal = true">立即创建</n-button>
          </template>
        </n-empty>
      </div>

      <!-- 配置列表 -->
      <n-data-table
        :columns="columns"
        :data="configs"
        :loading="loading"
        :bordered="false"
        class="mt-4"
      />
    </n-card>

    <!-- 创建/编辑模态框 -->
    <n-modal v-model:show="showCreateModal" @after-leave="resetForm">
      <n-card
        style="width: 560px"
        :title="editingId ? '编辑禅道配置' : '新增禅道配置'"
        :bordered="false"
        size="huge"
        role="dialog"
        aria-modal="true"
      >
        <n-form ref="formRef" :model="form" :rules="rules" label-placement="left" label-width="120">
          <n-form-item label="配置名称" path="config_name">
            <n-input v-model:value="form.config_name" placeholder="如：生产禅道" />
          </n-form-item>
          <n-form-item label="禅道地址" path="base_url">
            <n-input v-model:value="form.base_url" placeholder="http://zentao.example.com" />
          </n-form-item>
          <n-form-item label="登录账号" path="account">
            <n-input v-model:value="form.account" placeholder="禅道登录账号" />
          </n-form-item>
          <n-form-item label="登录密码" path="password">
            <n-input v-model:value="form.password" type="password" show-password-on="click" placeholder="禅道登录密码" />
          </n-form-item>
          <n-form-item label="默认产品ID">
            <n-input-number v-model:value="form.default_product_id" :min="1" style="width: 100%" placeholder="禅道中的产品ID（选填）" />
          </n-form-item>
          <n-form-item label="备注">
            <n-input v-model:value="form.description" type="textarea" :rows="2" placeholder="描述信息（选填）" />
          </n-form-item>
        </n-form>
        <template #footer>
          <n-space justify="end">
            <n-button @click="showCreateModal = false">取消</n-button>
            <n-button type="primary" :loading="saving" @click="handleSave">
              {{ editingId ? '保存' : '创建' }}
            </n-button>
          </n-space>
        </template>
      </n-card>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, h, onMounted } from 'vue'
import { useMessage, NButton, NSpace, NTag, NPopconfirm } from 'naive-ui'
import { zentaoAPI } from '@/api/zentao'

const message = useMessage()

// 状态
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const configs = ref([])
const activeConfig = ref(null)
const showCreateModal = ref(false)
const editingId = ref(null)
const formRef = ref(null)

const form = ref({
  config_name: '',
  base_url: '',
  account: '',
  password: '',
  default_product_id: null,
  description: ''
})

const rules = {
  config_name: { required: true, message: '请输入配置名称', trigger: 'blur' },
  base_url: { required: true, message: '请输入禅道地址', trigger: 'blur' },
  account: { required: true, message: '请输入登录账号', trigger: 'blur' },
  password: { required: true, message: '请输入登录密码', trigger: 'blur' }
}

// 表格列
const columns = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '配置名称', key: 'config_name', width: 140 },
  { title: '禅道地址', key: 'base_url', ellipsis: { tooltip: true } },
  { title: '账号', key: 'account', width: 100 },
  { title: '默认产品ID', key: 'default_product_id', width: 100 },
  {
    title: '状态', key: 'is_active', width: 80,
    render: (row) => h(NTag, { type: row.is_active ? 'success' : 'default', size: 'small' }, () => row.is_active ? '已激活' : '未激活')
  },
  { title: '创建时间', key: 'created_at', width: 170 },
  {
    title: '操作', key: 'actions', width: 260,
    render: (row) => h(NSpace, { size: 'small' }, () => [
      !row.is_active && h(NButton, { size: 'small', type: 'success', onClick: () => handleActivate(row.id) }, () => '激活'),
      h(NButton, { size: 'small', type: 'info', onClick: () => handleEdit(row) }, () => '编辑'),
      h(NPopconfirm, { onPositiveClick: () => handleDelete(row.id) }, {
        trigger: () => h(NButton, { size: 'small', type: 'error' }, () => '删除'),
        default: () => '确定要删除该配置吗？'
      })
    ])
  }
]

// 方法
async function loadData() {
  loading.value = true
  try {
    const [cfgRes, activeRes] = await Promise.all([
      zentaoAPI.getConfigs(),
      zentaoAPI.getActiveConfig()
    ])
    configs.value = cfgRes.data || []
    activeConfig.value = activeRes.data || null
  } catch (e) {
    message.error('加载配置失败')
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  try {
    await formRef.value?.validate()
  } catch { return }

  saving.value = true
  try {
    if (editingId.value) {
      await zentaoAPI.updateConfig(editingId.value, form.value)
      message.success('配置已更新')
    } else {
      await zentaoAPI.createConfig(form.value)
      message.success('配置已创建')
    }
    showCreateModal.value = false
    loadData()
  } catch (e) {
    message.error(e.response?.data?.detail || '操作失败')
  } finally {
    saving.value = false
  }
}

function handleEdit(row) {
  editingId.value = row.id
  form.value = {
    config_name: row.config_name,
    base_url: row.base_url,
    account: row.account,
    password: '',
    default_product_id: row.default_product_id,
    description: row.description || ''
  }
  showCreateModal.value = true
}

function resetForm() {
  editingId.value = null
  form.value = { config_name: '', base_url: '', account: '', password: '', default_product_id: null, description: '' }
}

async function handleDelete(id) {
  try {
    await zentaoAPI.deleteConfig(id)
    message.success('配置已删除')
    loadData()
  } catch (e) {
    message.error('删除失败')
  }
}

async function handleActivate(id) {
  try {
    await zentaoAPI.activateConfig(id)
    message.success('配置已激活')
    loadData()
  } catch (e) {
    message.error('激活失败')
  }
}

async function handleTestConnection() {
  if (!activeConfig.value) {
    message.warning('请先激活一个配置')
    return
  }
  testing.value = true
  try {
    const res = await zentaoAPI.testConnection()
    if (res.data?.success) {
      message.success(res.data.message || '连接成功')
    } else {
      message.error(res.data?.message || '连接失败')
    }
  } catch (e) {
    message.error(e.response?.data?.detail || '连接测试失败')
  } finally {
    testing.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.active-config {
  background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%);
  border: 1px solid #86efac;
  border-radius: 12px;
  padding: 20px 24px;
}
.active-badge {
  color: #16a34a;
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 12px;
}
.config-details {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 24px;
}
.detail-item {
  display: flex;
  align-items: center;
  font-size: 13px;
}
.detail-item .label {
  color: #6b7280;
  min-width: 100px;
}
.detail-item .value {
  color: #111827;
  font-weight: 500;
}
.no-active-tip {
  padding: 20px 0;
}
</style>
