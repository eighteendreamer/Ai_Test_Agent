<template>
  <div class="api-test-container">
    <!-- 步骤条 -->
    <n-card class="mb-4">
      <template #header>
        <div class="flex items-center gap-2">
          <i class="fas fa-plug text-xl" style="color:#007857"></i>
          <span class="text-lg font-bold">接口测试</span>
        </div>
      </template>
      <n-steps :current="currentStep" size="small">
        <n-step title="选择用例" description="勾选要测试的用例" />
        <n-step title="智能匹配" description="AI 匹配接口文件" />
        <n-step title="执行测试" description="确认并执行" />
      </n-steps>
    </n-card>

    <!-- Step 1: 选择用例 -->
    <div v-if="currentStep === 1">
      <n-card>
        <template #header>选择测试用例</template>
        <template #header-extra>
          <n-button type="primary" :disabled="selectedCaseIds.length === 0" @click="goToMatch">
            下一步：智能匹配
            <template #icon><i class="fas fa-arrow-right"></i></template>
          </n-button>
        </template>
        <div class="flex items-center gap-4 mb-4">
          <n-input v-model:value="caseSearch" placeholder="搜索用例标题..." clearable style="width:280px">
            <template #prefix><i class="fas fa-search text-gray-400"></i></template>
          </n-input>
          <span class="text-gray-400 text-sm">已选 {{ selectedCaseIds.length }} 条</span>
        </div>
        <n-spin :show="casesLoading">
          <n-data-table :columns="caseColumns" :data="filteredCases" :row-key="row => row.id"
            :checked-row-keys="selectedCaseIds" @update:checked-row-keys="keys => selectedCaseIds = keys"
            :max-height="480" size="small" striped />
        </n-spin>
      </n-card>
    </div>

    <!-- Step 2: 智能匹配 -->
    <div v-if="currentStep === 2">
      <n-card>
        <template #header>智能匹配结果</template>
        <template #header-extra>
          <div class="flex gap-2">
            <n-button @click="currentStep = 1">
              <template #icon><i class="fas fa-arrow-left"></i></template>上一步
            </n-button>
            <n-button type="primary" :disabled="!matchResult" :loading="matchingEndpoints" @click="goToExecute">
              {{ matchingEndpoints ? '匹配接口中...' : '下一步：确认执行' }}
              <template #icon><i class="fas fa-arrow-right"></i></template>
            </n-button>
          </div>
        </template>
        <n-spin :show="matching">
          <div v-if="matchResult">
            <!-- 推荐文件 -->
            <div class="match-label"><i class="fas fa-star" style="color:#f59e0b"></i> AI 推荐接口文件</div>
            <div class="match-card recommended">
              <div class="match-card-header">
                <div class="match-icon"><i class="fas fa-file-alt"></i></div>
                <div class="match-info">
                  <h4>{{ matchResult.recommended.original_filename }}</h4>
                  <span class="text-gray-400 text-xs">{{ matchResult.recommended.service_name || 'default' }}</span>
                </div>
                <n-tag type="success" size="small">置信度 {{ (matchResult.recommended.confidence * 100).toFixed(0) }}%</n-tag>
              </div>
              <p class="match-reason" v-if="matchResult.recommended.reason">
                <i class="fas fa-lightbulb text-yellow-500"></i> {{ matchResult.recommended.reason }}
              </p>
              <div class="match-stat">{{ matchResult.recommended.endpoint_count }} 个接口</div>
            </div>
            <!-- 候选 -->
            <div v-if="matchResult.candidates?.length" class="mt-4">
              <div class="match-label"><i class="fas fa-list text-gray-400"></i> 其他候选</div>
              <div class="candidate-list">
                <div v-for="c in matchResult.candidates" :key="c.spec_version_id" class="match-card candidate"
                  :class="{ active: selectedSpecVersionId === c.spec_version_id }" @click="selectCandidate(c)">
                  <div class="match-card-header">
                    <div class="match-icon small"><i class="fas fa-file"></i></div>
                    <div class="match-info"><h4>{{ c.original_filename }}</h4></div>
                    <n-tag size="small">{{ c.endpoint_count }} 接口</n-tag>
                  </div>
                </div>
              </div>
            </div>
            <!-- 接口预览 -->
            <div class="mt-4">
              <div class="match-label"><i class="fas fa-eye text-blue-500"></i> 接口预览</div>
              <n-data-table :columns="endpointPreviewColumns" :data="matchResult.preview_endpoints || []"
                size="small" :max-height="240" striped />
            </div>
          </div>
          <div v-else-if="!matching" class="text-center py-12 text-gray-400">
            <i class="fas fa-robot text-4xl mb-4 block"></i><p>正在等待匹配...</p>
          </div>
        </n-spin>
      </n-card>
    </div>

    <!-- Step 3: 确认执行 -->
    <div v-if="currentStep === 3">
      <n-card>
        <template #header>确认执行</template>
        <template #header-extra>
          <div class="flex gap-2">
            <n-button @click="currentStep = 2" :disabled="executing">
              <template #icon><i class="fas fa-arrow-left"></i></template>上一步
            </n-button>
            <n-button type="primary" :loading="executing" :disabled="executing" @click="doExecute">
              <template #icon><i class="fas fa-play"></i></template>
              {{ executing ? '执行中...' : '开始执行' }}
            </n-button>
          </div>
        </template>

        <!-- 执行前配置 -->
        <div v-if="!executeResult">
          <n-descriptions :column="2" label-placement="left" bordered size="small" class="mb-4">
            <n-descriptions-item label="选中用例">{{ selectedCaseIds.length }} 条</n-descriptions-item>
            <n-descriptions-item label="接口文件">{{ matchResult?.recommended?.original_filename || '-' }}</n-descriptions-item>
            <n-descriptions-item label="执行模式">
              <n-radio-group v-model:value="execMode" size="small">
                <n-radio-button value="llm_enhanced">LLM 增强</n-radio-button>
                <n-radio-button value="smoke_only">冒烟测试</n-radio-button>
              </n-radio-group>
            </n-descriptions-item>
            <n-descriptions-item label="接口数">{{ Object.keys(caseEndpointMap).length || matchResult?.recommended?.endpoint_count || 0 }} 个匹配</n-descriptions-item>
          </n-descriptions>

          <!-- 用例-接口匹配表 -->
          <n-card size="small" class="mb-4">
            <template #header>
              <span class="text-sm"><i class="fas fa-link mr-1" style="color:#007857"></i> 用例-接口匹配（AI 预匹配）</span>
            </template>
            <div class="endpoint-mapping-list">
              <div v-for="caseId in selectedCaseIds" :key="caseId" class="mapping-row">
                <div class="mapping-case">
                  <span class="mapping-case-id">#{{ caseId }}</span>
                  <span class="mapping-case-title">{{ getCaseTitle(caseId) }}</span>
                </div>
                <i class="fas fa-arrow-right text-gray-300 mx-2"></i>
                <div class="mapping-endpoint" v-if="caseEndpointMap[String(caseId)]">
                  <n-tag :type="methodColor(caseEndpointMap[String(caseId)].method)" size="small">
                    {{ caseEndpointMap[String(caseId)].method }}
                  </n-tag>
                  <code class="endpoint-path ml-1">{{ caseEndpointMap[String(caseId)].path }}</code>
                  <span class="endpoint-summary ml-2">{{ caseEndpointMap[String(caseId)].summary }}</span>
                </div>
                <div v-else class="text-gray-400 text-xs">未匹配</div>
              </div>
              <div v-if="!selectedCaseIds.length" class="text-gray-400 text-sm py-2">暂无用例</div>
            </div>
          </n-card>

          <n-card size="small" title="环境配置" class="mb-4" style="max-width:700px">
            <n-form label-placement="left" label-width="80" size="small">
              <n-form-item label="Base URL">
                <n-input v-model:value="envConfig.base_url" placeholder="http://localhost:8080" />
              </n-form-item>
              <n-form-item label="Headers">
                <n-input v-model:value="envConfig.headersStr" type="textarea" :rows="2"
                  placeholder='{"Authorization": "Bearer xxx"}' />
              </n-form-item>
            </n-form>
          </n-card>
        </div>

        <!-- 执行结果 -->
        <div v-if="executeResult">
          <!-- 统计摘要 -->
          <div class="result-summary">
            <div class="result-stat">
              <span class="result-stat-value total">{{ executeResult.summary.total }}</span>
              <span class="result-stat-label">总计</span>
            </div>
            <div class="result-stat">
              <span class="result-stat-value pass">{{ executeResult.summary.pass }}</span>
              <span class="result-stat-label">通过</span>
            </div>
            <div class="result-stat">
              <span class="result-stat-value fail">{{ executeResult.summary.fail }}</span>
              <span class="result-stat-label">失败</span>
            </div>
            <div class="result-stat">
              <span class="result-stat-value">{{ executeResult.summary.pass_rate }}%</span>
              <span class="result-stat-label">通过率</span>
            </div>
            <div class="result-stat">
              <span class="result-stat-value">{{ executeResult.summary.duration }}s</span>
              <span class="result-stat-label">耗时</span>
            </div>
          </div>

          <div class="flex gap-2 mb-4">
            <n-tag v-if="executeResult.report_id" type="info" size="small">
              <i class="fas fa-file-alt mr-1"></i> 报告 #{{ executeResult.report_id }}
            </n-tag>
            <n-tag v-if="executeResult.bug_report_ids?.length" type="error" size="small">
              <i class="fas fa-bug mr-1"></i> {{ executeResult.bug_report_ids.length }} 个 Bug
            </n-tag>
          </div>

          <!-- 每条用例的详细结果 -->
          <div v-for="(r, idx) in executeResult.results" :key="r.record_id" class="result-card" :class="r.status">
            <div class="result-card-header" @click="toggleDetail(idx)">
              <div class="flex items-center gap-3 flex-1 min-w-0">
                <n-tag :type="r.status === 'pass' ? 'success' : 'error'" size="small">
                  {{ r.status === 'pass' ? '通过' : '失败' }}
                </n-tag>
                <span class="result-title">{{ r.title }}</span>
              </div>
              <div class="flex items-center gap-4">
                <code class="result-endpoint">
                  <n-tag :type="methodColor(r.endpoint?.method)" size="tiny">{{ r.endpoint?.method || '?' }}</n-tag>
                  {{ r.endpoint?.path || '-' }}
                </code>
                <span class="text-gray-400 text-xs">{{ r.duration }}s</span>
                <i :class="expandedRows.includes(idx) ? 'fas fa-chevron-up' : 'fas fa-chevron-down'" class="text-gray-400"></i>
              </div>
            </div>

            <!-- 展开详情 -->
            <div v-if="expandedRows.includes(idx)" class="result-card-detail">
              <!-- 请求信息 -->
              <div class="detail-section">
                <div class="detail-label"><i class="fas fa-paper-plane text-blue-500"></i> 请求</div>
                <div class="detail-row">
                  <span class="detail-key">URL</span>
                  <code class="detail-value url">{{ r.request_info?.url || '-' }}</code>
                </div>
                <div v-if="r.request_info?.query_params && Object.keys(r.request_info.query_params).length" class="detail-row">
                  <span class="detail-key">Query</span>
                  <code class="detail-value">{{ JSON.stringify(r.request_info.query_params) }}</code>
                </div>
                <div v-if="r.request_info?.body" class="detail-row">
                  <span class="detail-key">Body</span>
                  <pre class="detail-json">{{ formatJson(r.request_info.body) }}</pre>
                </div>
                <div v-if="r.request_info?.headers && Object.keys(r.request_info.headers).length" class="detail-row">
                  <span class="detail-key">Headers</span>
                  <code class="detail-value text-xs">{{ JSON.stringify(r.request_info.headers) }}</code>
                </div>
              </div>

              <!-- 响应信息 -->
              <div class="detail-section">
                <div class="detail-label"><i class="fas fa-reply text-green-500"></i> 响应</div>
                <div class="detail-row">
                  <span class="detail-key">Status</span>
                  <n-tag :type="r.response_info?.status_code >= 200 && r.response_info?.status_code < 300 ? 'success' : 'error'" size="small">
                    {{ r.response_info?.status_code || '-' }}
                  </n-tag>
                </div>
                <div v-if="r.response_info?.body_trunc" class="detail-row">
                  <span class="detail-key">Body</span>
                  <pre class="detail-json">{{ formatResponseBody(r.response_info.body_trunc) }}</pre>
                </div>
              </div>

              <!-- 断言结果 -->
              <div v-if="r.assertions?.length" class="detail-section">
                <div class="detail-label"><i class="fas fa-check-double text-purple-500"></i> 断言</div>
                <div v-for="(a, ai) in r.assertions" :key="ai" class="assertion-row" :class="a.passed ? 'passed' : 'failed'">
                  <i :class="a.passed ? 'fas fa-check-circle text-green-500' : 'fas fa-times-circle text-red-500'"></i>
                  <span class="assertion-type">{{ a.type }}</span>
                  <span class="assertion-detail">期望: <code>{{ a.expected }}</code> 实际: <code>{{ a.actual }}</code></span>
                </div>
              </div>

              <!-- 错误信息 -->
              <div v-if="r.error_message" class="detail-section error-section">
                <div class="detail-label"><i class="fas fa-exclamation-triangle text-red-500"></i> 错误</div>
                <div class="error-text">{{ r.error_message }}</div>
              </div>
            </div>
          </div>

          <div class="flex justify-center mt-4">
            <n-button @click="resetAll">
              <template #icon><i class="fas fa-redo"></i></template>重新测试
            </n-button>
          </div>
        </div>
      </n-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, h } from 'vue'
import {
  NCard, NSteps, NStep, NButton, NInput, NDataTable, NTag, NSpin,
  NDescriptions, NDescriptionsItem, NForm, NFormItem, NRadioGroup, NRadioButton,
  useMessage
} from 'naive-ui'
import { testCaseAPI, apiTestAPI } from '@/api'

const message = useMessage()
const currentStep = ref(1)

// Step 1
const casesLoading = ref(false)
const caseList = ref([])
const selectedCaseIds = ref([])
const caseSearch = ref('')

const filteredCases = computed(() => {
  if (!caseSearch.value) return caseList.value
  const kw = caseSearch.value.toLowerCase()
  return caseList.value.filter(c => c.title.toLowerCase().includes(kw))
})

const caseColumns = [
  { type: 'selection' },
  { title: 'ID', key: 'id', width: 60 },
  { title: '标题', key: 'title', ellipsis: { tooltip: true } },
  { title: '模块', key: 'module', width: 100, ellipsis: { tooltip: true } },
  {
    title: '优先级', key: 'priority', width: 80,
    render(row) {
      const map = { '1': 'error', '2': 'warning', '3': 'info', '4': 'default' }
      const label = { '1': 'P1', '2': 'P2', '3': 'P3', '4': 'P4' }
      return h(NTag, { type: map[row.priority] || 'default', size: 'small' }, { default: () => label[row.priority] || row.priority })
    }
  },
  { title: '类型', key: 'case_type', width: 90, ellipsis: { tooltip: true } }
]

const loadCases = async () => {
  casesLoading.value = true
  try {
    const projectId = parseInt(localStorage.getItem('currentProjectId')) || null
    const res = await testCaseAPI.getList({ limit: 500, project_id: projectId })
    caseList.value = res.data || res.test_cases || []
  } catch (e) { message.error('加载用例失败') }
  finally { casesLoading.value = false }
}

// Step 2
const matching = ref(false)
const matchResult = ref(null)
const selectedSpecVersionId = ref(null)

const endpointPreviewColumns = [
  {
    title: 'Method', key: 'method', width: 90,
    render(row) {
      return h(NTag, { type: methodColor(row.method), size: 'small' }, { default: () => row.method })
    }
  },
  { title: 'Path', key: 'path', ellipsis: { tooltip: true } },
  { title: 'Summary', key: 'summary', ellipsis: { tooltip: true } }
]

const methodColor = (m) => {
  const map = { GET: 'success', POST: 'warning', PUT: 'info', DELETE: 'error', PATCH: 'default' }
  return map[m] || 'default'
}

const goToMatch = async () => {
  currentStep.value = 2
  matching.value = true
  matchResult.value = null
  try {
    const res = await apiTestAPI.matchSpec(selectedCaseIds.value)
    if (res.success) {
      matchResult.value = res.data
      selectedSpecVersionId.value = res.data.recommended.spec_version_id
    } else { message.error(res.message || '匹配失败') }
  } catch (e) {
    message.error(e.response?.data?.detail || e.message || '匹配失败')
  } finally { matching.value = false }
}

const selectCandidate = (c) => {
  selectedSpecVersionId.value = c.spec_version_id
  if (matchResult.value) {
    const old = matchResult.value.recommended
    matchResult.value.recommended = { ...c, confidence: c.confidence || 0.5, reason: '用户手动选择' }
    matchResult.value.candidates = matchResult.value.candidates.filter(x => x.spec_version_id !== c.spec_version_id)
    matchResult.value.candidates.unshift({
      spec_version_id: old.spec_version_id, original_filename: old.original_filename,
      minio_key: old.minio_key, service_name: old.service_name,
      endpoint_count: old.endpoint_count, confidence: old.confidence, reason: old.reason
    })
  }
}

// Step 3
const executing = ref(false)
const executeResult = ref(null)
const execMode = ref('llm_enhanced')
const envConfig = ref({ base_url: 'http://localhost:8080', headersStr: '' })
const expandedRows = ref([])
const caseEndpointMap = ref({})
const matchingEndpoints = ref(false)

const getCaseTitle = (id) => {
  const c = caseList.value.find(x => x.id === id)
  return c ? c.title : `用例 ${id}`
}

const goToExecute = async () => {
  matchingEndpoints.value = true
  try {
    const res = await apiTestAPI.matchEndpoints(selectedCaseIds.value, selectedSpecVersionId.value)
    if (res.success) {
      caseEndpointMap.value = res.data.case_endpoint_map || {}
      currentStep.value = 3
    } else {
      message.warning(res.message || '接口匹配失败，将使用全量匹配')
      caseEndpointMap.value = {}
      currentStep.value = 3
    }
  } catch (e) {
    message.warning('接口匹配失败，将使用全量匹配')
    caseEndpointMap.value = {}
    currentStep.value = 3
  } finally {
    matchingEndpoints.value = false
  }
}

const toggleDetail = (idx) => {
  const i = expandedRows.value.indexOf(idx)
  if (i >= 0) expandedRows.value.splice(i, 1)
  else expandedRows.value.push(idx)
}

const formatJson = (obj) => {
  try {
    if (typeof obj === 'string') return obj
    return JSON.stringify(obj, null, 2)
  } catch { return String(obj) }
}

const formatResponseBody = (text) => {
  if (!text) return '-'
  try {
    const parsed = JSON.parse(text)
    return JSON.stringify(parsed, null, 2)
  } catch { return text }
}

const doExecute = async () => {
  executing.value = true
  executeResult.value = null
  expandedRows.value = []

  let environment = null
  if (envConfig.value.base_url) {
    let headers = {}
    if (envConfig.value.headersStr) {
      try { headers = JSON.parse(envConfig.value.headersStr) } catch (e) { /* ignore */ }
    }
    environment = { base_url: envConfig.value.base_url, headers }
  }

  try {
    const res = await apiTestAPI.execute(
      selectedCaseIds.value, selectedSpecVersionId.value, environment, execMode.value,
      Object.keys(caseEndpointMap.value).length > 0 ? caseEndpointMap.value : null
    )
    if (res.success) {
      executeResult.value = res.data
      // 自动展开所有失败的
      res.data.results.forEach((r, i) => { if (r.status === 'fail') expandedRows.value.push(i) })
      const s = res.data.summary
      if (s.fail === 0) message.success(`全部通过 (${s.total} 条)`)
      else message.warning(`通过 ${s.pass}，失败 ${s.fail}`)
    } else { message.error(res.message || '执行失败') }
  } catch (e) {
    message.error(e.response?.data?.detail || e.message || '执行失败')
  } finally { executing.value = false }
}

const resetAll = () => {
  currentStep.value = 1
  selectedCaseIds.value = []
  matchResult.value = null
  selectedSpecVersionId.value = null
  executeResult.value = null
  expandedRows.value = []
  execMode.value = 'llm_enhanced'
  caseEndpointMap.value = {}
}

onMounted(loadCases)
</script>

<style scoped>
.match-label { font-size:13px; font-weight:600; color:#475569; margin-bottom:8px; display:flex; align-items:center; gap:6px; }
.match-card { border:1px solid #e2e8f0; border-radius:10px; padding:16px; transition:all 0.2s; }
.match-card.recommended { border-color:#007857; background:linear-gradient(135deg,rgba(0,120,87,0.03),rgba(0,166,126,0.03)); }
.match-card.candidate { cursor:pointer; padding:12px; }
.match-card.candidate:hover { border-color:#007857; background:rgba(0,120,87,0.02); }
.match-card.candidate.active { border-color:#007857; background:rgba(0,120,87,0.05); }
.match-card-header { display:flex; align-items:center; gap:12px; }
.match-icon { width:40px; height:40px; border-radius:8px; background:linear-gradient(135deg,#007857,#00a67e); color:white; display:flex; align-items:center; justify-content:center; font-size:16px; flex-shrink:0; }
.match-icon.small { width:32px; height:32px; font-size:13px; }
.match-info { flex:1; min-width:0; }
.match-info h4 { margin:0; font-size:14px; font-weight:600; color:#1e293b; }
.match-reason { margin:10px 0 0; font-size:13px; color:#64748b; line-height:1.5; }
.match-stat { margin-top:8px; font-size:12px; color:#94a3b8; }
.candidate-list { display:flex; flex-direction:column; gap:8px; }

/* 接口列表 */
.endpoint-list { display:flex; flex-direction:column; gap:6px; }
.endpoint-item { display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid #f1f5f9; font-size:13px; }
.endpoint-item:last-child { border-bottom:none; }
.endpoint-path { font-size:13px; color:#1e293b; font-weight:500; }
.endpoint-summary { color:#94a3b8; font-size:12px; margin-left:8px; }

/* 用例-接口匹配 */
.endpoint-mapping-list { display:flex; flex-direction:column; gap:4px; }
.mapping-row { display:flex; align-items:center; padding:8px 10px; border-bottom:1px solid #f1f5f9; font-size:13px; }
.mapping-row:last-child { border-bottom:none; }
.mapping-case { display:flex; align-items:center; gap:6px; min-width:200px; max-width:320px; }
.mapping-case-id { font-size:11px; color:#94a3b8; font-weight:600; flex-shrink:0; }
.mapping-case-title { color:#1e293b; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.mapping-endpoint { display:flex; align-items:center; gap:4px; flex:1; min-width:0; }

/* 结果统计 */
.result-summary { display:flex; gap:24px; margin-bottom:16px; padding:16px; background:#f8fafc; border-radius:10px; }
.result-stat { display:flex; flex-direction:column; align-items:center; }
.result-stat-value { font-size:24px; font-weight:700; color:#1e293b; }
.result-stat-value.total { color:#007857; }
.result-stat-value.pass { color:#16a34a; }
.result-stat-value.fail { color:#dc2626; }
.result-stat-label { font-size:12px; color:#94a3b8; margin-top:2px; }

/* 结果卡片 */
.result-card { border:1px solid #e2e8f0; border-radius:8px; margin-bottom:8px; overflow:hidden; transition:all 0.2s; }
.result-card.pass { border-left:3px solid #16a34a; }
.result-card.fail { border-left:3px solid #dc2626; }
.result-card-header { display:flex; align-items:center; justify-content:space-between; padding:10px 16px; cursor:pointer; gap:12px; }
.result-card-header:hover { background:#f8fafc; }
.result-title { font-size:13px; font-weight:500; color:#1e293b; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.result-endpoint { font-size:12px; color:#64748b; white-space:nowrap; display:flex; align-items:center; gap:4px; }

/* 展开详情 */
.result-card-detail { border-top:1px solid #f1f5f9; padding:16px; background:#fafbfc; }
.detail-section { margin-bottom:14px; }
.detail-section:last-child { margin-bottom:0; }
.detail-label { font-size:12px; font-weight:600; color:#475569; margin-bottom:6px; display:flex; align-items:center; gap:6px; }
.detail-row { display:flex; align-items:flex-start; gap:8px; margin-bottom:4px; padding:4px 0; }
.detail-key { font-size:11px; font-weight:600; color:#94a3b8; min-width:55px; text-align:right; padding-top:2px; }
.detail-value { font-size:12px; color:#334155; word-break:break-all; }
.detail-value.url { color:#007857; font-weight:500; }
.detail-json { font-size:12px; color:#334155; background:#f1f5f9; border-radius:6px; padding:8px 12px; margin:0; max-height:200px; overflow:auto; white-space:pre-wrap; word-break:break-all; font-family:'Courier New',monospace; line-height:1.5; }

/* 断言 */
.assertion-row { display:flex; align-items:center; gap:8px; padding:4px 0; font-size:12px; }
.assertion-type { font-weight:600; color:#475569; min-width:80px; }
.assertion-detail { color:#64748b; }
.assertion-detail code { background:#f1f5f9; padding:1px 4px; border-radius:3px; font-size:11px; }

/* 错误 */
.error-section { background:#fef2f2; border-radius:6px; padding:10px 12px; }
.error-text { font-size:12px; color:#dc2626; line-height:1.5; }
</style>
