<template>
  <div class="zentao-cases-container w-full h-full p-1 flex flex-col gap-1">
    <!-- 页面说明 -->
    <n-card class="shadow-sm rounded-lg" :bordered="false">
      <template #header-extra>
        <n-button type="primary" size="medium" :loading="importing" @click="showImportModal = true" class="px-6">
          <template #icon><i class="fas fa-cloud-download-alt"></i></template>
          开始导入
        </n-button>
      </template>
      <p class="text-gray-500 m-0">
        从禅道中一键拉取测试用例到本系统的用例库，导入后可在所有测试模块中无缝使用。<br>
        <span class="text-xs text-gray-400">目前支持按产品一键导入、按测试套件导入，或按用例 ID 精确导入，支持全量提取用例步骤和预期结果。</span>
      </p>
    </n-card>

    <!-- 禅道产品列表区块 -->
    <n-card class="shadow-sm rounded-lg flex-1 flex flex-col" :bordered="false" content-style="padding: 0; flex: 1; display: flex; flex-direction: column;">
      <div class="px-5 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
        <h3 class="text-base font-semibold m-0 text-gray-700">可用的禅道产品</h3>
        <n-button size="small" secondary type="primary" :loading="loadingProducts" @click="loadProducts" class="px-4">
          <template #icon><i class="fas fa-sync-alt"></i></template>
          刷新产品列表
        </n-button>
      </div>

      <div class="p-5 flex-1 overflow-hidden">
        <div v-if="products.length === 0 && !loadingProducts" class="h-64 flex flex-col items-center justify-center text-gray-400">
          <i class="fas fa-box-open text-4xl mb-3 opacity-50"></i>
          <p>暂无产品数据，请检查禅道配置或点击刷新</p>
        </div>
        
        <n-data-table
          v-else
          :columns="productColumns"
          :data="products"
          :loading="loadingProducts"
          :bordered="false"
          :bottom-bordered="false"
          size="medium"
          class="h-full"
          flex-height
          striped
        />
      </div>
    </n-card>

    <!-- 导入模态框 -->
    <n-modal v-model:show="showImportModal">
      <n-card style="width: 520px" title="从禅道导入用例" :bordered="false" size="huge" role="dialog" aria-modal="true">
        <n-form label-placement="left" label-width="120">
          <n-form-item label="导入方式">
            <n-radio-group v-model:value="importMode">
              <n-radio-button value="product">按产品</n-radio-button>
              <n-radio-button value="suite">按套件</n-radio-button>
              <n-radio-button value="ids">按用例ID</n-radio-button>
            </n-radio-group>
          </n-form-item>

          <n-form-item v-if="importMode === 'product'" label="产品ID">
            <n-input-number v-model:value="importForm.product_id" :min="1" style="width: 100%" placeholder="禅道产品ID" />
          </n-form-item>

          <n-form-item v-if="importMode === 'suite'" label="测试套件ID">
            <n-input-number v-model:value="importForm.suite_id" :min="1" style="width: 100%" placeholder="禅道测试套件ID" />
          </n-form-item>

          <n-form-item v-if="importMode === 'ids'" label="用例ID列表">
            <n-input
              v-model:value="caseIdsText"
              type="textarea"
              :rows="3"
              placeholder="输入用例ID，多个用逗号分隔，如：1,2,3"
            />
          </n-form-item>

          <n-form-item label="最多导入">
            <n-input-number
              v-model:value="importForm.limit"
              :min="0"
              style="width: 100%"
              placeholder="0 = 不限制"
            />
            <template #feedback>设为 0 表示导入全部，适合大量用例时先小批验证</template>
          </n-form-item>

          <n-form-item label="并发数">
            <n-input-number
              v-model:value="importForm.concurrency"
              :min="1"
              :max="20"
              style="width: 100%"
            />
            <template #feedback>同时拉取的详情请求数，默认 5，网络好可调至 10-15</template>
          </n-form-item>
        </n-form>

        <template #footer>
          <n-space justify="end">
            <n-button @click="showImportModal = false">取消</n-button>
            <n-button type="primary" :loading="importing" @click="handleImport">开始导入</n-button>
          </n-space>
        </template>
      </n-card>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, h, onMounted } from 'vue'
import { useMessage, NButton, NSpace, NButtonGroup } from 'naive-ui'
import { zentaoAPI } from '@/api/zentao'
import { importPlatformProject } from '@/api/testProject'

const message = useMessage()

const loadingProducts = ref(false)
const products = ref([])
const importing = ref(false)
const importingProjectId = ref(null)
const showImportModal = ref(false)
const importMode = ref('product')
const caseIdsText = ref('')

const importForm = ref({
  product_id: null,
  suite_id: null,
  case_ids: null,
  limit: 0,
  concurrency: 5
})

const productColumns = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '产品名称', key: 'name', ellipsis: { tooltip: true } },
  { title: '代号', key: 'code', width: 100 },
  { title: '状态', key: 'status', width: 80 },
  {
    title: '操作', key: 'actions', width: 240,
    render: (row) => h(
      NButtonGroup,
      { size: 'small' },
      {
        default: () => [
          h(NButton, {
            size: 'small',
            secondary: true,
            type: 'success',
            loading: importingProjectId.value === row.id,
            onClick: () => handleImportProject(row)
          }, () => '导入项目'),
          h(NButton, {
            size: 'small',
            type: 'primary',
            onClick: () => quickImport(row.id)
          }, () => '导入全部用例'),
        ]
      }
    )
  }
]

async function loadProducts() {
  loadingProducts.value = true
  try {
    const res = await zentaoAPI.getProducts()
    products.value = Array.isArray(res.data) ? res.data : []
  } catch (e) {
    message.error(e.response?.data?.detail || '获取产品列表失败，请先配置禅道连接')
  } finally {
    loadingProducts.value = false
  }
}

function quickImport(productId) {
  importMode.value = 'product'
  importForm.value.product_id = productId
  showImportModal.value = true
}

async function handleImportProject(product) {
  importingProjectId.value = product.id
  try {
    const res = await importPlatformProject({
      platform_id: 'zentao',
      source_id: product.id,
      source_name: product.name,
      source_code: product.code || '',
      description: `从禅道产品导入，本地项目可用于用例、报告、一键测试等模块。`
    })
    message.success(res.message || '项目导入成功')
  } catch (e) {
    message.error(e.response?.data?.detail || '导入项目失败')
  } finally {
    importingProjectId.value = null
  }
}

async function handleImport() {
  const payload = {}
  if (importMode.value === 'product') {
    if (!importForm.value.product_id) { message.warning('请输入产品ID'); return }
    payload.product_id = importForm.value.product_id
  } else if (importMode.value === 'suite') {
    if (!importForm.value.suite_id) { message.warning('请输入套件ID'); return }
    payload.suite_id = importForm.value.suite_id
  } else {
    const ids = caseIdsText.value.split(/[,，\s]+/).map(Number).filter(Boolean)
    if (ids.length === 0) { message.warning('请输入至少一个用例ID'); return }
    payload.case_ids = ids
  }

  if (importForm.value.limit > 0) payload.limit = importForm.value.limit
  payload.concurrency = importForm.value.concurrency || 5

  importing.value = true
  try {
    const res = await zentaoAPI.importCases(payload)
    message.success(res.data?.message || '导入成功')
    showImportModal.value = false
  } catch (e) {
    message.error(e.response?.data?.detail || '导入失败')
  } finally {
    importing.value = false
  }
}

onMounted(loadProducts)
</script>

<style scoped>
.zentao-cases-container {
  height: calc(100vh - 100px);
}
:deep(.n-data-table) {
  border-radius: 8px;
  overflow: hidden;
}
</style>
