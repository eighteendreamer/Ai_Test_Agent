<template>
  <div class="email-config-container">
    <n-card title="📧 邮件发送配置" :bordered="false">
      <template #header-extra>
        <n-button type="primary" @click="showCreateModal = true">
          <template #icon>
            <i class="fas fa-plus"></i>
          </template>
          新增配置
        </n-button>
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
            <span class="label">服务商：</span>
            <n-tag :type="{ resend: 'primary', aliyun: 'info', smtp: 'warning', cybermail: 'success' }[activeConfig.provider] || 'default'">
              {{ { resend: 'Resend', aliyun: '阿里云', smtp: 'SMTP 自定义', cybermail: 'CyberMail' }[activeConfig.provider] || activeConfig.provider }}
            </n-tag>
          </div>
          <div class="detail-item">
            <span class="label">发件人：</span>
            <span class="value">{{ activeConfig.sender_email }}</span>
          </div>
          <div class="detail-item">
            <span class="label">测试模式：</span>
            <n-tag :type="activeConfig.test_mode ? 'warning' : 'success'">
              {{ activeConfig.test_mode ? '开启' : '关闭' }}
            </n-tag>
          </div>
          <div v-if="activeConfig.test_mode" class="detail-item">
            <span class="label">测试邮箱：</span>
            <span class="value">{{ activeConfig.test_email }}</span>
          </div>
          <div v-if="activeConfig.provider === 'smtp'" class="detail-item">
            <span class="label">SMTP服务器：</span>
            <span class="value">{{ activeConfig.smtp_host }}:{{ activeConfig.smtp_port }}</span>
          </div>
        </div>
      </div>

      <!-- 配置列表 -->
      <n-data-table
        :columns="columns"
        :data="configs"
        :loading="loading"
        :bordered="false"
      />
    </n-card>

    <!-- 创建配置模态框 -->
    <n-modal v-model:show="showCreateModal">
      <n-card
        style="width: 600px"
        title="新增邮件配置"
        :bordered="false"
        size="huge"
        role="dialog"
        aria-modal="true"
      >
        <n-form
          ref="createFormRef"
          :model="createForm"
          :rules="rules"
          label-placement="left"
          label-width="120"
        >
          <n-form-item label="配置名称" path="config_name">
            <n-input v-model:value="createForm.config_name" placeholder="例如：生产环境" />
          </n-form-item>
          
          <n-form-item label="邮箱服务商" path="provider">
            <n-select
              v-model:value="createForm.provider"
              :options="providerOptions"
              placeholder="选择邮箱服务商"
            />
          </n-form-item>
          
          <n-form-item 
            v-if="createForm.provider !== 'smtp' && createForm.provider !== 'cybermail'"
            :label="createForm.provider === 'aliyun' ? 'Access Key ID' : 'API Key'" 
            path="api_key"
          >
            <n-input
              v-model:value="createForm.api_key"
              type="password"
              show-password-on="click"
              :placeholder="createForm.provider === 'aliyun' ? '阿里云 Access Key ID' : 'Resend API Key'"
            />
          </n-form-item>

          <!-- SMTP 自定义专属字段 -->
          <template v-if="createForm.provider === 'smtp'">
            <n-form-item label="SMTP服务器" path="smtp_host">
              <n-input v-model:value="createForm.smtp_host" placeholder="例如：mail.example.com" />
            </n-form-item>
            <n-form-item label="SMTP端口" path="smtp_port">
              <n-input-number v-model:value="createForm.smtp_port" :min="1" :max="65535" style="width:100%" />
            </n-form-item>
            <n-form-item label="SMTP用户名" path="smtp_username">
              <n-input v-model:value="createForm.smtp_username" placeholder="登录用户名，如未填则与发件人邮箱相同" />
            </n-form-item>
            <n-form-item label="SMTP密码" path="api_key">
              <n-input
                v-model:value="createForm.api_key"
                type="password"
                show-password-on="click"
                placeholder="SMTP登录密码"
              />
            </n-form-item>
          </template>

          <!-- CyberMail 专属字段（主机固定为 mail.cyberpersons.com:587） -->
          <template v-if="createForm.provider === 'cybermail'">
            <n-form-item label="SMTP用户名" path="smtp_username">
              <n-input v-model:value="createForm.smtp_username" placeholder="SMTP 登录用户名，如 smtp_xxxxxxxx" />
            </n-form-item>
            <n-form-item label="SMTP密码" path="api_key">
              <n-input
                v-model:value="createForm.api_key"
                type="password"
                show-password-on="click"
                placeholder="SMTP 登录密码"
              />
            </n-form-item>
            <div style="margin: -8px 0 12px 120px; font-size: 12px; color: #94a3b8;">
              服务器固定：mail.cyberpersons.com:587（STARTTLS）
            </div>
          </template>
          
          <n-form-item 
            v-if="createForm.provider === 'aliyun'" 
            label="Access Key Secret" 
            path="secret_key"
          >
            <n-input
              v-model:value="createForm.secret_key"
              type="password"
              show-password-on="click"
              placeholder="阿里云 Access Key Secret"
            />
          </n-form-item>
          
          <n-form-item label="发件人邮箱" path="sender_email">
            <n-input 
              v-model:value="createForm.sender_email" 
              :placeholder="createForm.provider === 'aliyun' ? '需在阿里云配置的发信地址' : 'onboarding@resend.dev'" 
            />
          </n-form-item>
          
          <n-form-item label="测试邮箱" path="test_email">
            <n-input v-model:value="createForm.test_email" placeholder="用于测试模式接收邮件" />
          </n-form-item>
          
          <n-form-item label="测试模式" path="test_mode">
            <n-switch v-model:value="testModeSwitch" />
            <span class="ml-2 text-xs text-gray-500">
              开启后所有邮件将发送到测试邮箱
            </span>
          </n-form-item>
          
          <n-form-item label="备注说明" path="description">
            <n-input
              v-model:value="createForm.description"
              type="textarea"
              :rows="3"
              placeholder="可选"
            />
          </n-form-item>
        </n-form>

        <template #footer>
          <div class="flex justify-end gap-2">
            <n-button @click="showCreateModal = false">取消</n-button>
            <n-button type="primary" :loading="creating" @click="handleCreate">
              创建
            </n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

    <!-- 编辑配置模态框 -->
    <n-modal v-model:show="showEditModal">
      <n-card
        style="width: 600px"
        title="编辑邮件配置"
        :bordered="false"
        size="huge"
        role="dialog"
        aria-modal="true"
      >
        <n-form
          ref="editFormRef"
          :model="editForm"
          label-placement="left"
          label-width="120"
        >
          <n-form-item label="API Key / SMTP密码">
            <n-input
              v-model:value="editForm.api_key"
              type="password"
              show-password-on="click"
              placeholder="留空则不修改"
            />
          </n-form-item>
          <n-form-item label="SMTP服务器">
            <n-input v-model:value="editForm.smtp_host" placeholder="留空则不修改" />
          </n-form-item>
          <n-form-item label="SMTP端口">
            <n-input-number v-model:value="editForm.smtp_port" :min="1" :max="65535" style="width:100%" placeholder="留空则不修改" />
          </n-form-item>
          <n-form-item label="SMTP用户名">
            <n-input v-model:value="editForm.smtp_username" placeholder="留空则不修改" />
          </n-form-item>
          
          <n-form-item label="发件人邮箱">
            <n-input v-model:value="editForm.sender_email" placeholder="留空则不修改" />
          </n-form-item>
          
          <n-form-item label="测试邮箱">
            <n-input v-model:value="editForm.test_email" placeholder="留空则不修改" />
          </n-form-item>
          
          <n-form-item label="测试模式">
            <n-switch v-model:value="editTestModeSwitch" />
          </n-form-item>
          
          <n-form-item label="备注说明">
            <n-input
              v-model:value="editForm.description"
              type="textarea"
              :rows="3"
            />
          </n-form-item>
        </n-form>

        <template #footer>
          <div class="flex justify-end gap-2">
            <n-button @click="showEditModal = false">取消</n-button>
            <n-button type="primary" :loading="editing" @click="handleEdit">
              保存
            </n-button>
          </div>
        </template>
      </n-card>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, onMounted, h, computed } from 'vue'
import {
  NCard,
  NButton,
  NDataTable,
  NTag,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSwitch,
  NSpace,
  NSelect,
  useMessage,
  useDialog
} from 'naive-ui'
import { emailAPI } from '@/api/index'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const configs = ref([])
const activeConfig = ref(null)

const showCreateModal = ref(false)
const showEditModal = ref(false)
const creating = ref(false)
const editing = ref(false)
const currentEditId = ref(null)

const createFormRef = ref(null)
const editFormRef = ref(null)

const createForm = ref({
  config_name: '',
  provider: 'resend',
  api_key: '',
  secret_key: '',
  sender_email: '',
  test_email: '',
  test_mode: 1,
  description: '',
  smtp_host: '',
  smtp_port: 587,
  smtp_username: ''
})

const editForm = ref({
  provider: '',
  api_key: '',
  secret_key: '',
  sender_email: '',
  test_email: '',
  test_mode: 1,
  description: '',
  smtp_host: '',
  smtp_port: null,
  smtp_username: ''
})

const providerOptions = [
  { label: 'Resend', value: 'resend' },
  { label: '阿里云邮件推送', value: 'aliyun' },
  { label: 'SMTP 自定义', value: 'smtp' },
  { label: 'CyberMail', value: 'cybermail' }
]

const testModeSwitch = computed({
  get: () => createForm.value.test_mode === 1,
  set: (val) => { createForm.value.test_mode = val ? 1 : 0 }
})

const editTestModeSwitch = computed({
  get: () => editForm.value.test_mode === 1,
  set: (val) => { editForm.value.test_mode = val ? 1 : 0 }
})

const rules = {
  config_name: [
    { required: true, message: '请输入配置名称', trigger: 'blur' }
  ],
  api_key: [
    { required: true, message: '请输入API Key', trigger: 'blur' }
  ],
  sender_email: [
    { required: true, message: '请输入发件人邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' }
  ]
}

const columns = [
  {
    title: '配置名称',
    key: 'config_name',
    width: 150
  },
  {
    title: '服务商',
    key: 'provider',
    width: 120,
    render(row) {
      return h(
        NTag,
        { type: { resend: 'primary', aliyun: 'info', smtp: 'warning', cybermail: 'success' }[row.provider] || 'default', size: 'small' },
        { default: () => ({ resend: 'Resend', aliyun: '阿里云', smtp: 'SMTP', cybermail: 'CyberMail' }[row.provider] || row.provider) }
      )
    }
  },
  {
    title: '发件人邮箱',
    key: 'sender_email'
  },
  {
    title: '测试模式',
    key: 'test_mode',
    width: 100,
    render(row) {
      return h(
        NTag,
        { type: row.test_mode ? 'warning' : 'success', size: 'small' },
        { default: () => row.test_mode ? '开启' : '关闭' }
      )
    }
  },
  {
    title: '状态',
    key: 'is_active',
    width: 100,
    render(row) {
      return h(
        NTag,
        { type: row.is_active ? 'success' : 'default', size: 'small' },
        { default: () => row.is_active ? '激活' : '未激活' }
      )
    }
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 160,
    render(row) {
      return new Date(row.created_at).toLocaleString('zh-CN')
    }
  },
  {
    title: '操作',
    key: 'actions',
    width: 220,
    render(row) {
      return h(
        NSpace,
        {},
        {
          default: () => [
            h(
              NButton,
              {
                size: 'small',
                type: row.is_active ? 'default' : 'primary',
                disabled: row.is_active === 1,
                onClick: () => activateConfig(row.id)
              },
              { default: () => row.is_active ? '已激活' : '激活' }
            ),
            h(
              NButton,
              {
                size: 'small',
                onClick: () => openEditModal(row)
              },
              { default: () => '编辑' }
            ),
            h(
              NButton,
              {
                size: 'small',
                type: 'error',
                disabled: row.is_active === 1,
                onClick: () => deleteConfig(row)
              },
              { default: () => '删除' }
            )
          ]
        }
      )
    }
  }
]

// 加载配置列表
const loadConfigs = async () => {
  loading.value = true
  try {
    const res = await emailAPI.getConfigs()
    configs.value = Array.isArray(res) ? res : []
    
    // 加载激活配置
    try {
      activeConfig.value = await emailAPI.getActiveConfig()
    } catch (error) {
      activeConfig.value = null
    }
  } catch (error) {
    console.error('加载配置失败:', error)
    message.error('加载配置失败')
  } finally {
    loading.value = false
  }
}

// 创建配置
const handleCreate = async () => {
  try {
    await createFormRef.value?.validate()
  } catch (error) {
    return
  }
  
  creating.value = true
  try {
    const payload = { ...createForm.value }
    if (!payload.test_email) {
      delete payload.test_email
    }
    if (!payload.description) {
      delete payload.description
    }
    if (payload.provider !== 'aliyun' && !payload.secret_key) {
      delete payload.secret_key
    }
    if (payload.provider !== 'smtp') {
      delete payload.smtp_host
      delete payload.smtp_port
    }
    if (payload.provider !== 'smtp' && payload.provider !== 'cybermail') {
      delete payload.smtp_username
    } else {
      if (!payload.smtp_username) delete payload.smtp_username
      if (payload.provider === 'smtp') {
        if (!payload.smtp_host) delete payload.smtp_host
      }
    }
    const res = await emailAPI.createConfig(payload)
    if (res.success) {
      message.success('配置创建成功')
      showCreateModal.value = false
      createForm.value = {
        config_name: '',
        provider: 'resend',
        api_key: '',
        secret_key: '',
        sender_email: '',
        test_email: '',
        test_mode: 1,
        description: '',
        smtp_host: '',
        smtp_port: 587,
        smtp_username: ''
      }
      loadConfigs()
    }
  } catch (error) {
    message.error('创建失败')
  } finally {
    creating.value = false
  }
}

// 打开编辑模态框
const openEditModal = (row) => {
  currentEditId.value = row.id
  editForm.value = {
    api_key: '',
    sender_email: row.sender_email,
    test_email: row.test_email,
    test_mode: row.test_mode,
    description: row.description,
    smtp_host: row.smtp_host || '',
    smtp_port: row.smtp_port || null,
    smtp_username: row.smtp_username || ''
  }
  showEditModal.value = true
}

// 编辑配置
const handleEdit = async () => {
  editing.value = true
  try {
    // 只发送非空字段
    const updateData = {}
    if (editForm.value.api_key) updateData.api_key = editForm.value.api_key
    if (editForm.value.sender_email) updateData.sender_email = editForm.value.sender_email
    if (editForm.value.test_email) updateData.test_email = editForm.value.test_email
    updateData.test_mode = editForm.value.test_mode
    if (editForm.value.description) updateData.description = editForm.value.description
    if (editForm.value.smtp_host) updateData.smtp_host = editForm.value.smtp_host
    if (editForm.value.smtp_port) updateData.smtp_port = editForm.value.smtp_port
    if (editForm.value.smtp_username) updateData.smtp_username = editForm.value.smtp_username
    
    const res = await emailAPI.updateConfig(currentEditId.value, updateData)
    if (res.success) {
      message.success('配置更新成功')
      showEditModal.value = false
      loadConfigs()
    }
  } catch (error) {
    message.error('更新失败')
  } finally {
    editing.value = false
  }
}

// 激活配置
const activateConfig = (id) => {
  dialog.warning({
    title: '确认激活',
    content: '激活此配置后，所有邮件将使用此配置发送。是否继续？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const res = await emailAPI.activateConfig(id)
        if (res.success) {
          message.success(res.message)
          loadConfigs()
        }
      } catch (error) {
        message.error('激活失败')
      }
    }
  })
}

// 删除配置
const deleteConfig = (row) => {
  dialog.error({
    title: '确认删除',
    content: `确定要删除配置"${row.config_name}"吗？此操作不可恢复。`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const res = await emailAPI.deleteConfig(row.id)
        if (res.success) {
          message.success('删除成功')
          loadConfigs()
        }
      } catch (error) {
        message.error(error.response?.data?.detail || '删除失败')
      }
    }
  })
}

onMounted(() => {
  loadConfigs()
})
</script>

<style scoped>

.active-config {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  padding: 20px;
  color: white;
}

.active-badge {
  display: inline-block;
  background: rgba(255, 255, 255, 0.2);
  padding: 8px 16px;
  border-radius: 20px;
  font-size: 14px;
  font-weight: bold;
  margin-bottom: 15px;
}

.config-details {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 15px;
}

.detail-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.detail-item .label {
  font-weight: 500;
  opacity: 0.9;
}

.detail-item .value {
  font-family: 'Courier New', monospace;
  background: rgba(255, 255, 255, 0.15);
  padding: 4px 8px;
  border-radius: 4px;
}
</style>
