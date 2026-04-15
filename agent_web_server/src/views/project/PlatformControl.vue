<template>
  <div class="platform-control">
    <n-card title="项目管理平台总控制台" :bordered="false">
      <n-space vertical :size="16">
        <n-alert type="info" :bordered="false">
          选择项目管理平台并配置连接信息，激活后即可在左侧菜单中使用。
        </n-alert>

        <n-grid :cols="3" :x-gap="16" :y-gap="16">
          <n-grid-item v-for="platform in supportedPlatforms" :key="platform.platform_id">
            <n-card
              :title="platform.platform_name"
              size="small"
              hoverable
              style="cursor: pointer"
              @click="selectPlatform(platform)"
            >
              <template #header-extra>
                <n-tag
                  v-if="getPlatformStatus(platform.platform_id)"
                  :type="getPlatformStatus(platform.platform_id).type"
                  size="small"
                >
                  {{ getPlatformStatus(platform.platform_id).text }}
                </n-tag>
              </template>
              <p class="text-sm text-gray-600">{{ platform.description }}</p>
            </n-card>
          </n-grid-item>
        </n-grid>
      </n-space>
    </n-card>

    <n-modal
      v-model:show="showConfigModal"
      preset="card"
      :title="`${selectedPlatform?.platform_name || ''} 配置`"
      style="width: 760px"
      :bordered="false"
      :segmented="{ content: 'soft', footer: 'soft' }"
    >
      <n-form ref="formRef" :model="formData" :rules="rules" label-placement="left" label-width="140">
        <n-form-item label="平台标识" path="platform_id">
          <n-input :value="selectedPlatform?.platform_id" disabled />
        </n-form-item>
        <n-form-item label="平台名称" path="platform_name">
          <n-input v-model:value="formData.platform_name" placeholder="可自定义显示名称" />
        </n-form-item>
        <n-form-item label="配置名称" path="config_name">
          <n-input v-model:value="formData.config_name" placeholder="例如：生产环境配置" />
        </n-form-item>
        <n-form-item label="平台地址" path="base_url">
          <n-input v-model:value="formData.base_url" placeholder="https://example.com" />
        </n-form-item>
        <n-form-item label="登录账号" path="account">
          <n-input v-model:value="formData.account" placeholder="用户名、邮箱或租户账号" />
        </n-form-item>
        <n-form-item label="登录密码" path="password">
          <n-input
            v-model:value="formData.password"
            type="password"
            placeholder="密码；如不修改可留空"
            show-password-on="click"
          />
        </n-form-item>

        <template v-if="selectedPlatform?.platform_id === 'zentao'">
          <n-form-item label="默认产品 ID" path="default_product_id">
            <n-input-number v-model:value="formData.default_product_id" :show-button="false" style="width: 100%" />
          </n-form-item>
          <n-form-item label="API 版本" path="api_version">
            <n-select v-model:value="formData.api_version" :options="apiVersionOptions" />
          </n-form-item>
        </template>

        <template v-if="selectedPlatform?.platform_id === 'pingcode'">
          <n-form-item label="鉴权方式">
            <n-select v-model:value="pingcodeExtra.auth_type" :options="pingcodeAuthOptions" />
          </n-form-item>
          <n-form-item label="Client ID">
            <n-input v-model:value="pingcodeExtra.client_id" placeholder="OAuth2 Client ID" />
          </n-form-item>
          <n-form-item label="Client Secret">
            <n-input
              v-model:value="pingcodeExtra.client_secret"
              type="password"
              placeholder="OAuth2 Client Secret"
              show-password-on="click"
            />
          </n-form-item>
          <n-form-item v-if="pingcodeExtra.auth_type === 'authorization_code'" label="回调地址">
            <n-input v-model:value="pingcodeExtra.redirect_uri" placeholder="https://your-app.com/oauth/callback" />
          </n-form-item>
        </template>

        <template v-if="selectedPlatform?.platform_id === 'worktile'">
          <n-form-item label="鉴权说明">
            <n-text depth="3" style="font-size: 13px">
              Worktile 采用 Authorization Code 流程，请先在平台申请 Client ID / Client Secret。
            </n-text>
          </n-form-item>
          <n-form-item label="Client ID">
            <n-input v-model:value="worktileExtra.client_id" placeholder="OAuth2 Client ID" />
          </n-form-item>
          <n-form-item label="Client Secret">
            <n-input
              v-model:value="worktileExtra.client_secret"
              type="password"
              placeholder="OAuth2 Client Secret"
              show-password-on="click"
            />
          </n-form-item>
          <n-form-item label="回调地址">
            <n-input v-model:value="worktileExtra.redirect_uri" placeholder="https://your-app.com/oauth/callback" />
          </n-form-item>
        </template>

        <template v-if="isGenericProjectPlatform(selectedPlatform?.platform_id)">
          <n-form-item label="鉴权方式">
            <n-select v-model:value="genericProjectExtra.auth_type" :options="genericProjectAuthOptions" />
          </n-form-item>
          <n-form-item v-if="genericProjectExtra.auth_type === 'header'" label="认证头名称">
            <n-input v-model:value="genericProjectExtra.auth_header_name" placeholder="例如：X-API-Key" />
          </n-form-item>
          <n-form-item
            v-if="genericProjectExtra.auth_type === 'header' || genericProjectExtra.auth_type === 'bearer'"
            label="认证前缀"
          >
            <n-input v-model:value="genericProjectExtra.auth_header_prefix" placeholder="例如：Bearer" />
          </n-form-item>
          <n-form-item label="项目接口路径">
            <n-input v-model:value="genericProjectExtra.project_path" placeholder="/api/projects 或 /v1.0/me/planner/plans" />
          </n-form-item>
          <n-form-item label="列表结果路径">
            <n-input v-model:value="genericProjectExtra.response_list_path" placeholder="data.items 或 value" />
          </n-form-item>
          <n-form-item label="ID 字段">
            <n-input v-model:value="genericProjectExtra.id_field" placeholder="id" />
          </n-form-item>
          <n-form-item label="名称字段">
            <n-input v-model:value="genericProjectExtra.name_field" placeholder="name / title" />
          </n-form-item>
          <n-form-item label="代号字段">
            <n-input v-model:value="genericProjectExtra.code_field" placeholder="code / key / id" />
          </n-form-item>
          <n-form-item label="状态字段">
            <n-input v-model:value="genericProjectExtra.status_field" placeholder="status / state / owner" />
          </n-form-item>
          <n-form-item label="范围字段">
            <n-input v-model:value="genericProjectExtra.scope_field" placeholder="可选，支持点路径" />
          </n-form-item>
          <n-form-item label="描述字段">
            <n-input v-model:value="genericProjectExtra.description_field" placeholder="description 或 container.url" />
          </n-form-item>
          <n-form-item label="自定义请求头">
            <n-input
              v-model:value="genericProjectExtra.custom_headers_text"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 4 }"
              placeholder='JSON，例如 {"X-Tenant":"demo"}'
            />
          </n-form-item>
          <n-form-item label="查询参数">
            <n-input
              v-model:value="genericProjectExtra.query_params_text"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 4 }"
              placeholder='JSON，例如 {"pageSize":100}'
            />
          </n-form-item>
          <n-alert type="info" :bordered="false" class="mb-2">
            {{ genericPlatformHint }}
          </n-alert>
        </template>

        <n-form-item v-if="needsApiToken(selectedPlatform?.platform_id)" label="API Token">
          <n-input v-model:value="formData.api_token" placeholder="某些平台使用 token 认证，可与密码二选一" />
        </n-form-item>

        <n-form-item label="备注说明">
          <n-input v-model:value="formData.description" type="textarea" placeholder="可选" />
        </n-form-item>

        <n-form-item label="启用状态">
          <n-space align="center">
            <n-switch
              v-model:value="formData.is_enabled"
              :checked-value="1"
              :unchecked-value="0"
            >
              <template #checked>在菜单中显示</template>
              <template #unchecked>在菜单中隐藏</template>
            </n-switch>
            <n-text depth="3" style="font-size: 12px">
              {{ formData.is_enabled ? '启用后会显示在左侧菜单' : '禁用后不会显示在左侧菜单' }}
            </n-text>
          </n-space>
        </n-form-item>
      </n-form>

      <template #footer>
        <n-space justify="space-between">
          <n-space>
            <n-button v-if="isEdit && !formData.is_active" type="success" @click="handleActivate">
              激活此配置
            </n-button>
            <n-button v-if="isEdit" type="error" @click="handleDelete">
              删除配置
            </n-button>
          </n-space>
          <n-space>
            <n-button @click="showConfigModal = false">取消</n-button>
            <n-button :loading="testing" @click="handleTestConnection">
              测试连接
            </n-button>
            <n-button type="primary" :loading="submitting" @click="handleSubmit">
              {{ isEdit ? '保存配置' : '创建配置' }}
            </n-button>
          </n-space>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NGrid,
  NGridItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  NText,
  useMessage
} from 'naive-ui'
import {
  activatePlatform,
  createPlatform,
  deletePlatform,
  getPlatform,
  getSupportedPlatforms,
  listPlatforms,
  testConnection,
  updatePlatform
} from '@/api/project'

const message = useMessage()
const supportedPlatforms = ref([])
const configuredPlatforms = ref([])
const selectedPlatform = ref(null)
const showConfigModal = ref(false)
const submitting = ref(false)
const testing = ref(false)
const isEdit = ref(false)
const formRef = ref(null)

const pingcodeExtra = ref({
  auth_type: 'client_credentials',
  client_id: '',
  client_secret: '',
  redirect_uri: ''
})

const worktileExtra = ref({
  client_id: '',
  client_secret: '',
  redirect_uri: ''
})

const createGenericProjectExtra = (platformId = '') => {
  if (platformId === 'msproject') {
    return {
      auth_type: 'bearer',
      auth_header_name: 'Authorization',
      auth_header_prefix: 'Bearer',
      project_path: '/v1.0/me/planner/plans',
      response_list_path: 'value',
      id_field: 'id',
      name_field: 'title',
      code_field: 'id',
      status_field: 'owner',
      scope_field: '',
      description_field: 'container.url',
      custom_headers_text: '',
      query_params_text: ''
    }
  }

  return {
    auth_type: 'basic',
    auth_header_name: 'Authorization',
    auth_header_prefix: '',
    project_path: '/api/projects',
    response_list_path: 'data.items',
    id_field: 'id',
    name_field: 'name',
    code_field: 'code',
    status_field: 'status',
    scope_field: '',
    description_field: 'description',
    custom_headers_text: '',
    query_params_text: ''
  }
}

const genericProjectExtra = ref(createGenericProjectExtra())

const pingcodeAuthOptions = [
  { label: 'Client Credentials', value: 'client_credentials' },
  { label: 'Authorization Code', value: 'authorization_code' }
]

const genericProjectAuthOptions = [
  { label: 'Basic Auth', value: 'basic' },
  { label: 'Bearer Token', value: 'bearer' },
  { label: 'Custom Header', value: 'header' },
  { label: 'No Auth', value: 'none' }
]

const formData = ref({
  platform_id: '',
  platform_name: '',
  config_name: '',
  base_url: '',
  account: '',
  password: '',
  api_token: '',
  default_product_id: null,
  api_version: 'v2',
  description: '',
  is_enabled: 1,
  is_active: 0
})

const isGenericProjectPlatform = (platformId) => ['8manage', 'msproject'].includes(platformId)

const rules = {
  platform_name: { required: true, message: '请输入平台名称', trigger: 'blur' },
  config_name: { required: true, message: '请输入配置名称', trigger: 'blur' },
  base_url: { required: true, message: '请输入平台地址', trigger: 'blur' },
  account: {
    trigger: 'blur',
    validator: () => {
      const platformId = selectedPlatform.value?.platform_id
      if (isGenericProjectPlatform(platformId) && genericProjectExtra.value.auth_type !== 'basic') return true
      return formData.value.account ? true : new Error('请输入登录账号')
    }
  },
  password: {
    trigger: 'blur',
    validator: () => {
      const platformId = selectedPlatform.value?.platform_id
      if (isGenericProjectPlatform(platformId)) {
        if (genericProjectExtra.value.auth_type === 'basic') {
          return formData.value.password ? true : new Error('请输入登录密码')
        }
        return true
      }
      return formData.value.password || formData.value.api_token ? true : new Error('请输入登录密码')
    }
  }
}

const apiVersionOptions = [
  { label: 'API v1', value: 'v1' },
  { label: 'API v2', value: 'v2' }
]

const genericPlatformHint = computed(() => {
  if (selectedPlatform.value?.platform_id === 'msproject') {
    return 'Microsoft Project 默认按 Microsoft Graph Planner 接口预填。如果你们接的是自建服务，可以直接改项目接口路径、列表结果路径和字段映射。'
  }
  return '8Manage 默认按通用 REST 项目接口预填。若是私有化接口，只要把路径、列表结果路径和字段映射改成实际值即可。'
})

const needsApiToken = (platformId) => {
  return ['pingcode', 'worktile', 'ones', 'jira', 'asana', 'clickup', '8manage', 'msproject'].includes(platformId)
}

const getPlatformStatus = (platformId) => {
  const config = configuredPlatforms.value.find(item => item.platform_id === platformId)
  if (!config) return null
  if (config.is_active) return { type: 'success', text: '已激活' }
  if (config.is_enabled) return { type: 'info', text: '已配置' }
  return { type: 'default', text: '已禁用' }
}

const parseJsonText = (text, fallback = {}) => {
  if (!text || !String(text).trim()) return fallback
  try {
    return JSON.parse(text)
  } catch {
    return fallback
  }
}

const stringifyJsonText = (value) => {
  if (!value || (typeof value === 'object' && Object.keys(value).length === 0)) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return ''
  }
}

const serializeGenericProjectExtra = () => ({
  auth_type: genericProjectExtra.value.auth_type,
  auth_header_name: genericProjectExtra.value.auth_header_name,
  auth_header_prefix: genericProjectExtra.value.auth_header_prefix,
  project_path: genericProjectExtra.value.project_path,
  response_list_path: genericProjectExtra.value.response_list_path,
  id_field: genericProjectExtra.value.id_field,
  name_field: genericProjectExtra.value.name_field,
  code_field: genericProjectExtra.value.code_field,
  status_field: genericProjectExtra.value.status_field,
  scope_field: genericProjectExtra.value.scope_field,
  description_field: genericProjectExtra.value.description_field,
  custom_headers: parseJsonText(genericProjectExtra.value.custom_headers_text, {}),
  query_params: parseJsonText(genericProjectExtra.value.query_params_text, {})
})

const applyExtraConfigToState = (platformId, extra) => {
  if (platformId === 'pingcode') {
    pingcodeExtra.value = {
      auth_type: 'client_credentials',
      client_id: '',
      client_secret: '',
      redirect_uri: '',
      ...(extra || {})
    }
    return
  }

  if (platformId === 'worktile') {
    worktileExtra.value = {
      client_id: '',
      client_secret: '',
      redirect_uri: '',
      ...(extra || {})
    }
    return
  }

  if (isGenericProjectPlatform(platformId)) {
    const defaults = createGenericProjectExtra(platformId)
    genericProjectExtra.value = {
      ...defaults,
      ...(extra || {}),
      custom_headers_text: stringifyJsonText(extra?.custom_headers),
      query_params_text: stringifyJsonText(extra?.query_params)
    }
    return
  }

  genericProjectExtra.value = createGenericProjectExtra(platformId)
}

const getExtraConfig = (platformId) => {
  if (platformId === 'pingcode') return JSON.stringify(pingcodeExtra.value)
  if (platformId === 'worktile') return JSON.stringify(worktileExtra.value)
  if (isGenericProjectPlatform(platformId)) return JSON.stringify(serializeGenericProjectExtra())
  return formData.value.extra_config
}

const resetForm = (platformId = '') => {
  formData.value = {
    platform_id: platformId,
    platform_name: '',
    config_name: '',
    base_url: '',
    account: '',
    password: '',
    api_token: '',
    default_product_id: null,
    api_version: 'v2',
    description: '',
    is_enabled: 1,
    is_active: 0
  }
  pingcodeExtra.value = {
    auth_type: 'client_credentials',
    client_id: '',
    client_secret: '',
    redirect_uri: ''
  }
  worktileExtra.value = {
    client_id: '',
    client_secret: '',
    redirect_uri: ''
  }
  genericProjectExtra.value = createGenericProjectExtra(platformId)
  isEdit.value = false
}

const selectPlatform = async (platform) => {
  selectedPlatform.value = platform
  showConfigModal.value = true
  resetForm(platform.platform_id)
  formData.value.platform_name = platform.platform_name

  try {
    const res = await getPlatform(platform.platform_id)
    if (res.success && res.data) {
      isEdit.value = true
      formData.value = {
        ...res.data,
        password: '',
        is_enabled: res.data.is_enabled || 0,
        is_active: res.data.is_active || 0
      }
      const extra = typeof res.data.extra_config === 'string'
        ? parseJsonText(res.data.extra_config, {})
        : (res.data.extra_config || {})
      applyExtraConfigToState(platform.platform_id, extra)
      return
    }

    applyExtraConfigToState(platform.platform_id, {})
  } catch (error) {
    console.error('加载平台配置失败:', error)
    applyExtraConfigToState(platform.platform_id, {})
  }
}

const loadSupportedPlatforms = async () => {
  try {
    const res = await getSupportedPlatforms()
    if (res.success) supportedPlatforms.value = res.data
  } catch (error) {
    message.error(`加载平台列表失败：${error.message}`)
  }
}

const loadConfiguredPlatforms = async () => {
  try {
    const res = await listPlatforms()
    if (res.success) configuredPlatforms.value = res.data
  } catch (error) {
    console.error('加载已配置平台失败:', error)
  }
}

const handleSubmit = async () => {
  try {
    await formRef.value?.validate()
    submitting.value = true

    const data = {
      platform_id: selectedPlatform.value.platform_id,
      platform_name: formData.value.platform_name,
      config_name: formData.value.config_name,
      base_url: formData.value.base_url,
      account: formData.value.account,
      password: formData.value.password,
      api_token: formData.value.api_token,
      default_product_id: formData.value.default_product_id,
      api_version: formData.value.api_version,
      description: formData.value.description,
      is_enabled: formData.value.is_enabled,
      is_active: formData.value.is_active,
      extra_config: getExtraConfig(selectedPlatform.value.platform_id)
    }

    if (isEdit.value && !data.password) delete data.password

    const res = isEdit.value
      ? await updatePlatform(formData.value.id, data)
      : await createPlatform(data)

    if (res.success) {
      message.success(res.message || (isEdit.value ? '更新成功' : '创建成功'))
      showConfigModal.value = false
      selectedPlatform.value = null
      await loadConfiguredPlatforms()
      window.location.reload()
    }
  } catch (error) {
    if (error.message) {
      message.error(`${isEdit.value ? '更新' : '创建'}失败：${error.message}`)
    }
  } finally {
    submitting.value = false
  }
}

const handleActivate = async () => {
  try {
    const res = await activatePlatform(formData.value.id)
    if (res.success) {
      message.success(res.message)
      formData.value.is_active = 1
      await loadConfiguredPlatforms()
      window.location.reload()
    }
  } catch (error) {
    message.error(`激活失败：${error.message}`)
  }
}

const handleTestConnection = async () => {
  if (!formData.value.base_url) {
    message.warning('请先填写平台地址')
    return
  }

  testing.value = true
  try {
    const res = await testConnection({
      base_url: formData.value.base_url,
      account: formData.value.account,
      password: formData.value.password,
      api_token: formData.value.api_token,
      platform_id: selectedPlatform.value?.platform_id,
      api_version: formData.value.api_version,
      extra_config: getExtraConfig(selectedPlatform.value?.platform_id)
    })
    if (res.success) {
      message.success(res.message || '连接成功')
    } else {
      message.error(res.message || '连接失败')
    }
  } catch (error) {
    message.error(`测试失败：${error.message}`)
  } finally {
    testing.value = false
  }
}

const handleDelete = async () => {
  if (!confirm(`确定要删除 "${formData.value.platform_name}" 的配置吗？`)) return

  try {
    const res = await deletePlatform(formData.value.id)
    if (res.success) {
      message.success(res.message)
      showConfigModal.value = false
      selectedPlatform.value = null
      await loadConfiguredPlatforms()
      window.location.reload()
    }
  } catch (error) {
    message.error(`删除失败：${error.message}`)
  }
}

onMounted(() => {
  loadSupportedPlatforms()
  loadConfiguredPlatforms()
})
</script>

<style scoped>
.platform-control {
  max-width: 1400px;
}
</style>
