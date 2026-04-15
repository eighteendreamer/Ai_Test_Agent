<template>
  <div class="spec-manage-container">
    <!-- 顶部操作栏 -->
    <n-card>
      <template #header>
        <div class="flex items-center gap-2">
          <i class="fas fa-file-code text-xl" style="color:#007857"></i>
          <span class="text-lg font-bold">接口文件管理</span>
        </div>
      </template>
      <template #header-extra>
        <n-button type="primary" @click="showImportModal = true">
          <template #icon><i class="fas fa-cloud-upload-alt"></i></template>
          导入接口文档
        </n-button>
      </template>
      <p class="text-gray-500">支持多种格式导入：Postman、Swagger、Redoc、HAR、ApiFox、cURL、Markdown 等</p>
    </n-card>

    <!-- 筛选 -->
    <div class="flex items-center gap-4 mt-4 mb-4">
      <n-input v-model:value="searchText" placeholder="搜索文件名..." clearable style="width:280px">
        <template #prefix><i class="fas fa-search text-gray-400"></i></template>
      </n-input>
      <n-select v-model:value="filterService" :options="serviceOptions" clearable placeholder="按服务筛选" style="width:180px" />
      <div class="flex-1"></div>
      <span class="text-gray-400 text-sm">共 {{ filteredList.length }} 个文件</span>
    </div>

    <!-- 卡片列表 -->
    <n-spin :show="loading">
      <div v-if="filteredList.length === 0 && !loading" class="text-center py-16 text-gray-400">
        <i class="fas fa-inbox text-5xl mb-4 block"></i>
        <p>暂无接口文件，点击右上角导入</p>
      </div>
      <div v-else class="spec-grid">
        <div v-for="spec in filteredList" :key="spec.id" class="spec-card">
          <div class="spec-card-header">
            <div class="spec-icon">
              <i class="fas fa-file-alt"></i>
            </div>
            <div class="spec-meta">
              <h4 class="spec-filename" :title="spec.original_filename">{{ spec.original_filename }}</h4>
              <span class="spec-service">{{ spec.service_name || 'default' }}</span>
            </div>
          </div>
          <div class="spec-card-body">
            <div class="spec-stat-row">
              <div class="spec-stat">
                <span class="spec-stat-value">{{ spec.endpoint_count }}</span>
                <span class="spec-stat-label">接口数</span>
              </div>
              <div class="spec-stat">
                <span class="spec-stat-value">{{ formatSize(spec.file_size) }}</span>
                <span class="spec-stat-label">文件大小</span>
              </div>
            </div>
            <div v-if="spec.base_url" class="spec-base-url">
              <i class="fas fa-link"></i>
              <span>{{ spec.base_url }}</span>
            </div>
            <div v-if="spec.parse_warnings && spec.parse_warnings.length > 0" class="spec-warning">
              <i class="fas fa-exclamation-triangle"></i>
              {{ spec.parse_warnings[0] }}
            </div>
            <p class="spec-time">{{ formatTime(spec.created_at) }}</p>
          </div>
          <div class="spec-card-footer">
            <n-button size="small" quaternary @click="viewDetail(spec)">
              <template #icon><i class="fas fa-eye"></i></template>
              查看
            </n-button>
            <n-button size="small" quaternary @click="viewContent(spec)">
              <template #icon><i class="fas fa-code"></i></template>
              原文
            </n-button>
            <n-button size="small" quaternary type="error" @click="confirmDelete(spec)">
              <template #icon><i class="fas fa-trash"></i></template>
              删除
            </n-button>
          </div>
        </div>
      </div>
    </n-spin>

    <!-- 导入弹窗 -->
    <n-modal v-model:show="showImportModal" preset="card" title="导入接口文档" style="width:600px">
      <n-tabs v-model:value="importMode" type="segment" animated>
        <n-tab-pane name="file" tab="📁 文件上传">
          <n-form label-placement="left" label-width="90" class="mt-4">
            <n-form-item label="服务名称">
              <n-input v-model:value="uploadForm.serviceName" placeholder="可选，用于分组管理" />
            </n-form-item>
            <n-form-item label="测试地址">
              <n-input v-model:value="uploadForm.baseUrl" placeholder="例如: https://api.example.com" clearable />
              <div class="text-xs text-gray-400 mt-2">
                用于接口测试时拼接完整URL
              </div>
            </n-form-item>
            <n-form-item label="选择文件">
              <n-upload
                :max="1"
                accept=".md,.json,.yaml,.yml,.har"
                :default-upload="false"
                @change="handleFileChange"
              >
                <n-button>
                  <template #icon><i class="fas fa-paperclip"></i></template>
                  选择文件
                </n-button>
              </n-upload>
              <div class="text-xs text-gray-400 mt-2">
                支持：Postman (.json)、Swagger (.json/.yaml)、HAR (.har)、ApiFox (.json)、Markdown (.md)
              </div>
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <n-tab-pane name="url" tab="🔗 URL 导入">
          <n-form label-placement="left" label-width="90" class="mt-4">
            <n-form-item label="服务名称">
              <n-input v-model:value="urlForm.serviceName" placeholder="可选，用于分组管理" />
            </n-form-item>
            <n-form-item label="文档 URL">
              <n-input
                v-model:value="urlForm.url"
                type="textarea"
                placeholder="输入 API 文档 URL&#10;例如：https://api.example.com/swagger.json"
                :autosize="{ minRows: 3, maxRows: 5 }"
                @blur="extractBaseUrlFromUrl"
              />
              <div class="text-xs text-gray-400 mt-2">
                支持：Swagger JSON/YAML、Swagger UI 页面、Redoc 页面、在线 Markdown
              </div>
            </n-form-item>
            <n-form-item label="测试地址">
              <n-input v-model:value="urlForm.baseUrl" placeholder="自动识别或手动输入" clearable />
              <div class="text-xs text-gray-400 mt-2">
                从URL自动提取，可手动修改
              </div>
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <n-tab-pane name="text" tab="📋 文本粘贴">
          <n-form label-placement="left" label-width="90" class="mt-4">
            <n-form-item label="服务名称">
              <n-input v-model:value="textForm.serviceName" placeholder="可选，用于分组管理" />
            </n-form-item>
            <n-form-item label="测试地址">
              <n-input v-model:value="textForm.baseUrl" placeholder="例如: https://api.example.com" clearable />
              <div class="text-xs text-gray-400 mt-2">
                用于接口测试时拼接完整URL
              </div>
            </n-form-item>
            <n-form-item label="格式提示">
              <n-select
                v-model:value="textForm.formatHint"
                :options="formatOptions"
                placeholder="自动检测"
                clearable
              />
            </n-form-item>
            <n-form-item label="文本内容">
              <n-input
                v-model:value="textForm.content"
                type="textarea"
                placeholder="粘贴 cURL 命令、JSON 或 Markdown 内容"
                :autosize="{ minRows: 8, maxRows: 15 }"
              />
              <div class="text-xs text-gray-400 mt-2">
                支持：cURL 命令、Postman JSON、Swagger JSON、Markdown
              </div>
            </n-form-item>
          </n-form>
        </n-tab-pane>
      </n-tabs>

      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="showImportModal = false">取消</n-button>
          <n-button type="primary" :loading="importing" :disabled="!canImport" @click="doImport">
            <template #icon><i class="fas fa-upload"></i></template>
            导入并解析
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 详情弹窗 -->
    <n-modal v-model:show="showDetailModal" preset="card" title="接口文件详情" style="width:800px">
      <n-spin :show="detailLoading">
        <div v-if="currentDetail">
          <n-descriptions :column="2" label-placement="left" bordered size="small" class="mb-4">
            <n-descriptions-item label="文件名">{{ currentDetail.original_filename }}</n-descriptions-item>
            <n-descriptions-item label="服务">{{ currentDetail.service_name || 'default' }}</n-descriptions-item>
            <n-descriptions-item label="接口数">{{ currentDetail.endpoint_count }}</n-descriptions-item>
            <n-descriptions-item label="MinIO Key">
              <span class="text-xs text-gray-400 break-all">{{ currentDetail.minio_key }}</span>
            </n-descriptions-item>
          </n-descriptions>

          <h4 class="font-bold mb-2">接口列表</h4>
          <n-data-table
            :columns="endpointColumns"
            :data="currentDetail.endpoints || []"
            :max-height="360"
            size="small"
            striped
          />
        </div>
      </n-spin>
    </n-modal>

    <!-- 原文弹窗 -->
    <n-modal v-model:show="showContentModal" preset="card" title="Markdown 原文" style="width:800px">
      <n-spin :show="contentLoading">
        <div class="markdown-raw">
          <pre>{{ rawContent }}</pre>
        </div>
      </n-spin>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, h } from 'vue'
import {
  NCard, NButton, NInput, NSelect, NModal, NForm, NFormItem, NUpload, NTabs, NTabPane,
  NDescriptions, NDescriptionsItem, NDataTable, NTag, NSpin, useMessage, useDialog
} from 'naive-ui'
import { specAPI } from '@/api'

const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const specList = ref([])
const searchText = ref('')
const filterService = ref(null)

// 导入
const showImportModal = ref(false)
const importing = ref(false)
const importMode = ref('file')  // file, url, text

// 文件上传表单
const uploadForm = ref({ serviceName: '', baseUrl: '', file: null })

// URL 导入表单
const urlForm = ref({ serviceName: '', baseUrl: '', url: '' })

// 文本粘贴表单
const textForm = ref({ serviceName: '', baseUrl: '', formatHint: null, content: '' })

// 格式选项
const formatOptions = [
  { label: 'cURL 命令', value: 'curl' },
  { label: 'JSON', value: 'json' },
  { label: 'Markdown', value: 'markdown' }
]

// 详情
const showDetailModal = ref(false)
const detailLoading = ref(false)
const currentDetail = ref(null)

// 原文
const showContentModal = ref(false)
const contentLoading = ref(false)
const rawContent = ref('')

// 服务选项
const serviceOptions = computed(() => {
  const names = [...new Set(specList.value.map(s => s.service_name).filter(Boolean))]
  return names.map(n => ({ label: n, value: n }))
})

// 筛选
const filteredList = computed(() => {
  let list = specList.value
  if (filterService.value) {
    list = list.filter(s => s.service_name === filterService.value)
  }
  if (searchText.value) {
    const kw = searchText.value.toLowerCase()
    list = list.filter(s => s.original_filename.toLowerCase().includes(kw))
  }
  return list
})

// 是否可以导入
const canImport = computed(() => {
  if (importMode.value === 'file') {
    return uploadForm.value.file !== null
  } else if (importMode.value === 'url') {
    return urlForm.value.url.trim() !== ''
  } else if (importMode.value === 'text') {
    return textForm.value.content.trim() !== ''
  }
  return false
})

// endpoint 表格列
const endpointColumns = [
  {
    title: 'Method', key: 'method', width: 90,
    render(row) {
      const colorMap = { GET: 'success', POST: 'warning', PUT: 'info', DELETE: 'error', PATCH: 'default' }
      return h(NTag, { type: colorMap[row.method] || 'default', size: 'small' }, { default: () => row.method })
    }
  },
  { title: 'Path', key: 'path', ellipsis: { tooltip: true } },
  { title: 'Summary', key: 'summary', ellipsis: { tooltip: true } }
]

const loadList = async () => {
  loading.value = true
  try {
    // 获取当前选中的项目ID
    const projectId = localStorage.getItem('currentProjectId')
    const params = { limit: 200 }
    if (projectId) {
      params.project_id = parseInt(projectId)
    }
    const res = await specAPI.getList(params)
    if (res.success) specList.value = res.data
  } catch (e) {
    message.error('加载失败')
  } finally {
    loading.value = false
  }
}

const handleFileChange = ({ fileList }) => {
  uploadForm.value.file = fileList.length > 0 ? fileList[0].file : null
}

const extractBaseUrlFromUrl = () => {
  if (!urlForm.value.url) return
  try {
    const url = new URL(urlForm.value.url.trim())
    urlForm.value.baseUrl = `${url.protocol}//${url.host}`
  } catch (e) {
    // Invalid URL, do nothing
  }
}

const doImport = async () => {
  importing.value = true
  try {
    let res
    
    if (importMode.value === 'file') {
      // 文件上传
      res = await specAPI.import(
        uploadForm.value.file, 
        uploadForm.value.serviceName || undefined,
        uploadForm.value.baseUrl || undefined
      )
    } else if (importMode.value === 'url') {
      // URL 导入
      res = await specAPI.importFromUrl(
        urlForm.value.url, 
        urlForm.value.serviceName || undefined,
        urlForm.value.baseUrl || undefined
      )
    } else if (importMode.value === 'text') {
      // 文本粘贴
      res = await specAPI.importFromText(
        textForm.value.content,
        textForm.value.formatHint || undefined,
        textForm.value.serviceName || undefined,
        textForm.value.baseUrl || undefined
      )
    }
    
    if (res.success) {
      message.success(`导入成功，解析出 ${res.data.endpoint_count} 个接口`)
      showImportModal.value = false
      
      // 重置表单
      uploadForm.value = { serviceName: '', baseUrl: '', file: null }
      urlForm.value = { serviceName: '', baseUrl: '', url: '' }
      textForm.value = { serviceName: '', baseUrl: '', formatHint: null, content: '' }
      
      await loadList()
    } else {
      message.error(res.message || '导入失败')
    }
  } catch (e) {
    const msg = e.response?.data?.detail || e.message || '导入失败'
    message.error(msg)
  } finally {
    importing.value = false
  }
}

const viewDetail = async (spec) => {
  showDetailModal.value = true
  detailLoading.value = true
  try {
    const res = await specAPI.getDetail(spec.id)
    if (res.success) currentDetail.value = res.data
  } catch (e) {
    message.error('获取详情失败')
  } finally {
    detailLoading.value = false
  }
}

const viewContent = async (spec) => {
  showContentModal.value = true
  contentLoading.value = true
  rawContent.value = ''
  try {
    const res = await specAPI.getContent(spec.id)
    if (res.success) rawContent.value = res.data.content
  } catch (e) {
    message.error('获取原文失败')
  } finally {
    contentLoading.value = false
  }
}

const confirmDelete = (spec) => {
  dialog.warning({
    title: '确认删除',
    content: `确定删除 "${spec.original_filename}" 吗？MinIO 中的文件也会被删除。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const res = await specAPI.delete(spec.id)
        if (res.success) {
          message.success('删除成功')
          await loadList()
        }
      } catch (e) {
        message.error('删除失败')
      }
    }
  })
}

const formatSize = (bytes) => {
  if (!bytes) return '0B'
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1024 / 1024).toFixed(1) + 'MB'
}

const formatTime = (iso) => {
  if (!iso) return '-'
  return new Date(iso).toLocaleString()
}

onMounted(loadList)
</script>

<style scoped>
.spec-manage-container {
  padding: 0;
}

.spec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.spec-card {
  background: white;
  border: 1px solid rgba(0, 120, 87, 0.1);
  border-radius: 12px;
  overflow: hidden;
  transition: transform 0.2s, box-shadow 0.2s;
}

.spec-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
}

.spec-card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 16px 0;
}

.spec-icon {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: linear-gradient(135deg, #007857, #00a67e);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.spec-meta {
  flex: 1;
  min-width: 0;
}

.spec-filename {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.spec-service {
  font-size: 12px;
  color: #94a3b8;
}

.spec-card-body {
  padding: 12px 16px;
}

.spec-stat-row {
  display: flex;
  gap: 24px;
  margin-bottom: 8px;
}

.spec-stat {
  display: flex;
  flex-direction: column;
}

.spec-stat-value {
  font-size: 18px;
  font-weight: 700;
  color: #007857;
}

.spec-stat-label {
  font-size: 11px;
  color: #94a3b8;
}

.spec-base-url {
  font-size: 12px;
  color: #007857;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.spec-base-url i {
  font-size: 11px;
}

.spec-base-url span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.spec-warning {
  font-size: 12px;
  color: #f0a020;
  margin-bottom: 4px;
}

.spec-warning i {
  margin-right: 4px;
}

.spec-time {
  font-size: 12px;
  color: #cbd5e1;
  margin: 0;
}

.spec-card-footer {
  display: flex;
  justify-content: space-around;
  border-top: 1px solid #f1f5f9;
  padding: 8px 0;
}

.markdown-raw {
  max-height: 500px;
  overflow: auto;
  background: #f8fafc;
  border-radius: 8px;
  padding: 16px;
}

.markdown-raw pre {
  margin: 0;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: #334155;
}
</style>
