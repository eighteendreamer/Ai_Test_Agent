<template>
  <div class="presstest-container">
    <!-- 快速开始卡片 -->
    <div class="quick-start-card">
      <div class="card-title">
        <i class="fas fa-rocket"></i>
        <span>快速开始</span>
      </div>
      
      <n-steps :current="currentStep" :status="stepStatus">
        <n-step title="选择用例" description="选择要压测的测试用例" />
        <n-step title="配置参数" description="设置压测引擎和参数" />
        <n-step title="执行测试" description="开始执行压测任务" />
      </n-steps>

      <!-- 步骤 1: 选择用例 -->
      <div v-show="currentStep === 1" class="step-content">
        <div class="section-header">
          <i class="fas fa-list-check"></i>
          <span>选择测试用例（可多选）</span>
        </div>
        
        <n-data-table
          :columns="caseColumns"
          :data="testCases"
          :loading="loadingCases"
          :row-key="row => row.id"
          :checked-row-keys="selectedCaseIds"
          @update:checked-row-keys="handleCaseSelection"
          :max-height="400"
          :pagination="casePagination"
        />

        <div class="step-actions">
          <n-button type="primary" @click="nextStep" :disabled="selectedCaseIds.length === 0">
            下一步：配置参数
            <template #icon><i class="fas fa-arrow-right"></i></template>
          </n-button>
        </div>
      </div>

      <!-- 步骤 2: 配置参数 -->
      <div v-show="currentStep === 2" class="step-content">
        <div class="config-grid">
          <!-- 左侧：引擎选择 -->
          <div class="config-section">
            <div class="section-header">
              <i class="fas fa-cogs"></i>
              <span>选择压测引擎</span>
            </div>
            
            <div class="engine-cards">
              <div 
                v-for="engine in engines" 
                :key="engine.value"
                class="engine-card"
                :class="{ active: selectedEngine === engine.value }"
                @click="selectedEngine = engine.value"
              >
                <div class="engine-icon" :style="{ background: engine.color }">
                  <i :class="engine.icon"></i>
                </div>
                <div class="engine-info">
                  <div class="engine-name">{{ engine.name }}</div>
                  <div class="engine-desc">{{ engine.description }}</div>
                  <div class="engine-tags">
                    <n-tag v-for="tag in engine.tags" :key="tag" size="small" :bordered="false">
                      {{ tag }}
                    </n-tag>
                  </div>
                </div>
                <div class="engine-check">
                  <i v-if="selectedEngine === engine.value" class="fas fa-check-circle"></i>
                </div>
              </div>
            </div>
          </div>

          <!-- 右侧：压测参数 -->
          <div class="config-section">
            <div class="section-header">
              <i class="fas fa-sliders"></i>
              <span>压测参数</span>
            </div>
            
            <n-form :model="pressConfig" label-placement="left" label-width="120">
              <n-form-item label="任务名称">
                <n-input v-model:value="pressConfig.name" placeholder="输入任务名称" />
              </n-form-item>
              
              <n-form-item label="虚拟用户数">
                <n-input-number 
                  v-model:value="pressConfig.vus" 
                  :min="1" 
                  :max="10000"
                  style="width: 100%"
                >
                  <template #suffix>用户</template>
                </n-input-number>
              </n-form-item>
              
              <n-form-item label="持续时间">
                <n-input-group>
                  <n-input-number 
                    v-model:value="pressConfig.durationValue" 
                    :min="1"
                    style="width: 70%"
                  />
                  <n-select 
                    v-model:value="pressConfig.durationUnit" 
                    :options="durationUnits"
                    style="width: 30%"
                  />
                </n-input-group>
              </n-form-item>
              
              <n-form-item label="负载模式">
                <n-select 
                  v-model:value="pressConfig.pattern" 
                  :options="loadPatterns"
                />
              </n-form-item>
              
              <n-form-item label="思考时间">
                <n-input-number 
                  v-model:value="pressConfig.thinkTime" 
                  :min="0"
                  style="width: 100%"
                >
                  <template #suffix>秒</template>
                </n-input-number>
              </n-form-item>
            </n-form>
          </div>
        </div>

        <div class="step-actions">
          <n-button @click="prevStep">
            <template #icon><i class="fas fa-arrow-left"></i></template>
            上一步
          </n-button>
          <n-button type="primary" @click="nextStep">
            下一步：执行测试
            <template #icon><i class="fas fa-arrow-right"></i></template>
          </n-button>
        </div>
      </div>

      <!-- 步骤 3: 确认执行 -->
      <div v-show="currentStep === 3" class="step-content">
        <div class="confirm-section">
          <div class="confirm-header">
            <i class="fas fa-clipboard-check"></i>
            <span>确认压测配置</span>
          </div>
          
          <n-descriptions :column="2" bordered>
            <n-descriptions-item label="任务名称">{{ pressConfig.name }}</n-descriptions-item>
            <n-descriptions-item label="压测引擎">
              <n-tag :type="getEngineTagType(selectedEngine)">
                {{ getEngineName(selectedEngine) }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="测试用例数">{{ selectedCaseIds.length }} 个</n-descriptions-item>
            <n-descriptions-item label="虚拟用户数">{{ pressConfig.vus }} 用户</n-descriptions-item>
            <n-descriptions-item label="持续时间">
              {{ pressConfig.durationValue }}{{ pressConfig.durationUnit }}
            </n-descriptions-item>
            <n-descriptions-item label="负载模式">{{ getPatternText(pressConfig.pattern) }}</n-descriptions-item>
          </n-descriptions>

          <div class="selected-cases">
            <div class="section-header">
              <i class="fas fa-list"></i>
              <span>已选用例</span>
            </div>
            <n-list bordered>
              <n-list-item v-for="caseItem in getSelectedCases()" :key="caseItem.id">
                <div class="case-item">
                  <div class="case-title">{{ caseItem.title }}</div>
                  <div class="case-meta">
                    <n-tag size="small">{{ caseItem.module }}</n-tag>
                    <span class="case-priority">优先级: {{ caseItem.priority }}</span>
                  </div>
                </div>
              </n-list-item>
            </n-list>
          </div>
        </div>

        <div class="step-actions">
          <n-button @click="prevStep">
            <template #icon><i class="fas fa-arrow-left"></i></template>
            上一步
          </n-button>
          <n-button type="primary" @click="executeTest" :loading="executing">
            <template #icon><i class="fas fa-play"></i></template>
            开始执行压测
          </n-button>
        </div>
      </div>
    </div>

    <!-- 任务列表 -->
    <div class="tasks-card">
      <div class="card-title">
        <i class="fas fa-history"></i>
        <span>任务列表</span>
      </div>
      
      <n-data-table
        :columns="taskColumns"
        :data="tasks"
        :loading="loadingTasks"
        :pagination="taskPagination"
      />
    </div>

    <!-- 任务详情弹窗 -->
    <n-modal 
      v-model:show="showDetailModal" 
      preset="card" 
      title="任务详情" 
      style="width: 1000px;"
      :segmented="{ content: 'soft' }"
    >
      <div v-if="currentTask">
        <n-tabs type="line" animated>
          <n-tab-pane name="overview" tab="概览">
            <n-descriptions :column="2" bordered>
              <n-descriptions-item label="任务名称">{{ currentTask.name }}</n-descriptions-item>
              <n-descriptions-item label="引擎">
                <n-tag :type="getEngineTagType(currentTask.engine)">
                  {{ currentTask.engine?.toUpperCase() }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="状态">
                <n-tag :type="getStatusType(currentTask.status)">
                  {{ getStatusText(currentTask.status) }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="创建时间">{{ formatTime(currentTask.created_at) }}</n-descriptions-item>
              <n-descriptions-item label="开始时间">{{ formatTime(currentTask.started_at) }}</n-descriptions-item>
              <n-descriptions-item label="完成时间">{{ formatTime(currentTask.finished_at) }}</n-descriptions-item>
            </n-descriptions>
          </n-tab-pane>

          <n-tab-pane name="metrics" tab="性能指标" v-if="taskResult">
            <div class="metrics-grid">
              <div class="metric-card">
                <div class="metric-icon" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
                  <i class="fas fa-chart-line"></i>
                </div>
                <div class="metric-content">
                  <div class="metric-label">总请求数</div>
                  <div class="metric-value">{{ taskResult.total_requests.toLocaleString() }}</div>
                </div>
              </div>
              
              <div class="metric-card">
                <div class="metric-icon" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);">
                  <i class="fas fa-tachometer-alt"></i>
                </div>
                <div class="metric-content">
                  <div class="metric-label">平均 RPS</div>
                  <div class="metric-value">{{ taskResult.avg_rps.toFixed(2) }}</div>
                </div>
              </div>
              
              <div class="metric-card">
                <div class="metric-icon" style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);">
                  <i class="fas fa-clock"></i>
                </div>
                <div class="metric-content">
                  <div class="metric-label">平均响应时间</div>
                  <div class="metric-value">{{ taskResult.avg_response_time.toFixed(2) }} ms</div>
                </div>
              </div>
              
              <div class="metric-card">
                <div class="metric-icon" style="background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);">
                  <i class="fas fa-exclamation-triangle"></i>
                </div>
                <div class="metric-content">
                  <div class="metric-label">错误率</div>
                  <div class="metric-value">{{ taskResult.error_rate.toFixed(2) }}%</div>
                </div>
              </div>
            </div>

            <n-divider />

            <n-descriptions :column="3" bordered>
              <n-descriptions-item label="P95 响应时间">{{ taskResult.p95_response_time.toFixed(2) }} ms</n-descriptions-item>
              <n-descriptions-item label="P99 响应时间">{{ taskResult.p99_response_time.toFixed(2) }} ms</n-descriptions-item>
              <n-descriptions-item label="最大 RPS">{{ taskResult.max_rps.toFixed(2) }}</n-descriptions-item>
            </n-descriptions>
          </n-tab-pane>

          <n-tab-pane name="analysis" tab="AI 分析" v-if="taskResult?.ai_analysis">
            <n-alert type="info" :bordered="false">
              <template #icon><i class="fas fa-robot"></i></template>
              {{ taskResult.ai_analysis }}
            </n-alert>

            <div v-if="taskResult.recommendations?.length" style="margin-top: 20px;">
              <div class="section-header">
                <i class="fas fa-lightbulb"></i>
                <span>优化建议</span>
              </div>
              <n-list bordered>
                <n-list-item v-for="(rec, idx) in taskResult.recommendations" :key="idx">
                  <template #prefix>
                    <n-icon color="#f0a020" size="20">
                      <i class="fas fa-lightbulb"></i>
                    </n-icon>
                  </template>
                  {{ rec }}
                </n-list-item>
              </n-list>
            </div>
          </n-tab-pane>
        </n-tabs>
      </div>
      
      <template #footer>
        <n-space justify="end">
          <n-button @click="showDetailModal = false">关闭</n-button>
          <n-button v-if="currentTask?.status === 'finished'" type="primary" @click="sendReport">
            <template #icon><i class="fas fa-envelope"></i></template>
            发送报告
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, reactive, h, onMounted, computed } from 'vue'
import { NButton, NTag, useMessage } from 'naive-ui'
import axios from 'axios'

const message = useMessage()

// ============================================================
// 步骤控制
// ============================================================
const currentStep = ref(1)
const stepStatus = ref('process')
const executing = ref(false)

const nextStep = () => {
  if (currentStep.value < 3) {
    currentStep.value++
  }
}

const prevStep = () => {
  if (currentStep.value > 1) {
    currentStep.value--
  }
}

// ============================================================
// 步骤 1: 测试用例选择
// ============================================================
const testCases = ref([])
const selectedCaseIds = ref([])
const loadingCases = ref(false)

const caseColumns = [
  { type: 'selection' },
  { title: 'ID', key: 'id', width: 80 },
  { title: '用例标题', key: 'title', ellipsis: { tooltip: true } },
  { title: '所属模块', key: 'module', width: 120 },
  { 
    title: '优先级', 
    key: 'priority', 
    width: 100,
    render: (row) => h(NTag, { 
      type: row.priority === '1' ? 'error' : row.priority === '2' ? 'warning' : 'default',
      size: 'small'
    }, { default: () => `P${row.priority}` })
  },
  { title: '用例类型', key: 'case_type', width: 120 }
]

const casePagination = reactive({
  page: 1,
  pageSize: 10,
  showSizePicker: true,
  pageSizes: [10, 20, 50]
})

const loadTestCases = async () => {
  loadingCases.value = true
  try {
    // 获取当前选中的项目ID
    const projectId = parseInt(localStorage.getItem('currentProjectId')) || null
    
    const res = await axios.get('/api/test-cases/list', {
      params: {
        limit: 100,
        offset: 0,
        case_type: '性能测试',
        project_id: projectId  // 传递项目ID
      }
    })
    testCases.value = res.data.data || []
  } catch (error) {
    message.error('加载测试用例失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    loadingCases.value = false
  }
}

const handleCaseSelection = (keys) => {
  selectedCaseIds.value = keys
}

const getSelectedCases = () => {
  return testCases.value.filter(c => selectedCaseIds.value.includes(c.id))
}

// ============================================================
// 步骤 2: 引擎选择和参数配置
// ============================================================
const selectedEngine = ref('k6')

const engines = [
  {
    value: 'k6',
    name: 'k6',
    icon: 'fas fa-bolt',
    color: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    description: 'HTTP 接口高并发压测',
    tags: ['高性能', 'JavaScript', '云原生']
  },
  {
    value: 'locust',
    name: 'Locust',
    icon: 'fas fa-bug',
    color: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
    description: '业务流程压测（登录/下单）',
    tags: ['Python', '分布式', '易扩展']
  },
  {
    value: 'jmeter',
    name: 'JMeter',
    icon: 'fas fa-flask',
    color: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
    description: 'TCP / WebSocket / 数据库',
    tags: ['多协议', 'GUI', '企业级']
  }
]

const pressConfig = reactive({
  name: '',
  vus: 100,
  durationValue: 1,
  durationUnit: 'm',
  pattern: 'constant',
  thinkTime: 0
})

const durationUnits = [
  { label: '秒', value: 's' },
  { label: '分钟', value: 'm' },
  { label: '小时', value: 'h' }
]

const loadPatterns = [
  { label: '恒定负载', value: 'constant' },
  { label: '阶梯增长', value: 'ramp_up' },
  { label: '尖峰测试', value: 'spike' },
  { label: '压力测试', value: 'stress' }
]

// ============================================================
// 步骤 3: 执行测试
// ============================================================
const executeTest = async () => {
  if (!pressConfig.name) {
    message.warning('请输入任务名称')
    return
  }

  executing.value = true
  try {
    // 获取当前选中的项目ID
    const projectId = parseInt(localStorage.getItem('currentProjectId')) || 1
    
    // 构建 DSL
    const selectedCases = getSelectedCases()
    const duration = `${pressConfig.durationValue}${pressConfig.durationUnit}`
    
    // 为每个用例创建一个压测任务（简化版，实际可能需要合并）
    const taskPromises = selectedCases.map(async (testCase) => {
      const dsl = {
        name: `${pressConfig.name} - ${testCase.title}`,
        type: 'api',
        engine: selectedEngine.value,
        request: {
          method: 'POST',
          url: '/api/test',  // 这里需要从用例中提取实际URL
          headers: { 'Content-Type': 'application/json' },
          body: testCase.test_data || {},
          timeout: 30
        },
        load: {
          vus: pressConfig.vus,
          duration: duration,
          pattern: pressConfig.pattern
        },
        think_time: pressConfig.thinkTime,
        description: testCase.title
      }
      
      return axios.post('/api/presstest/tasks', {
        project_id: projectId,  // 传递项目ID
        name: dsl.name,
        dsl: dsl
      })
    })
    
    await Promise.all(taskPromises)
    
    message.success(`成功创建 ${selectedCases.length} 个压测任务，正在后台执行...`)
    
    // 重置状态
    currentStep.value = 1
    selectedCaseIds.value = []
    pressConfig.name = ''
    pressConfig.vus = 100
    pressConfig.durationValue = 1
    pressConfig.durationUnit = 'm'
    pressConfig.pattern = 'constant'
    pressConfig.thinkTime = 0
    
    // 刷新任务列表
    loadTasks()
    
  } catch (error) {
    message.error('创建任务失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    executing.value = false
  }
}

// ============================================================
// 任务列表
// ============================================================
const tasks = ref([])
const loadingTasks = ref(false)
const showDetailModal = ref(false)
const currentTask = ref(null)
const taskResult = ref(null)

const taskColumns = [
  { title: 'ID', key: 'id', width: 80 },
  { title: '任务名称', key: 'name', ellipsis: { tooltip: true } },
  {
    title: '引擎',
    key: 'engine',
    width: 100,
    render: (row) => h(NTag, { 
      type: getEngineTagType(row.engine),
      size: 'small'
    }, { default: () => row.engine?.toUpperCase() })
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => h(NTag, { 
      type: getStatusType(row.status),
      size: 'small'
    }, { default: () => getStatusText(row.status) })
  },
  { 
    title: '创建时间', 
    key: 'created_at', 
    width: 180, 
    render: (row) => formatTime(row.created_at) 
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: (row) => h(
      NButton,
      {
        size: 'small',
        type: 'primary',
        text: true,
        onClick: () => viewDetail(row)
      },
      { default: () => '查看详情' }
    )
  }
]

const taskPagination = reactive({
  page: 1,
  pageSize: 10,
  showSizePicker: true,
  pageSizes: [10, 20, 50]
})

const loadTasks = async () => {
  loadingTasks.value = true
  try {
    // 获取当前选中的项目ID
    const projectId = parseInt(localStorage.getItem('currentProjectId')) || null
    
    const res = await axios.get('/api/presstest/tasks', {
      params: {
        limit: taskPagination.pageSize,
        offset: (taskPagination.page - 1) * taskPagination.pageSize,
        project_id: projectId  // 传递项目ID
      }
    })
    tasks.value = res.data
  } catch (error) {
    message.error('加载任务列表失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    loadingTasks.value = false
  }
}

const viewDetail = async (task) => {
  currentTask.value = task
  taskResult.value = null
  showDetailModal.value = true
  
  // 如果任务已完成，加载结果
  if (task.status === 'finished') {
    try {
      const res = await axios.get(`/api/presstest/tasks/${task.id}/result`)
      taskResult.value = res.data
    } catch (error) {
      message.error('加载任务结果失败')
    }
  }
}

const sendReport = async () => {
  if (!currentTask.value) return
  
  try {
    const res = await axios.post(`/api/presstest/tasks/${currentTask.value.id}/send-report`)
    message.success(res.data.message)
  } catch (error) {
    message.error('发送报告失败: ' + (error.response?.data?.detail || error.message))
  }
}

// ============================================================
// 辅助函数
// ============================================================
const getEngineTagType = (engine) => {
  const types = { k6: 'success', locust: 'warning', jmeter: 'info' }
  return types[engine] || 'default'
}

const getEngineName = (engine) => {
  const names = { k6: 'k6', locust: 'Locust', jmeter: 'JMeter' }
  return names[engine]?.toUpperCase() || engine?.toUpperCase()
}

const getStatusType = (status) => {
  const types = {
    created: 'default',
    waiting: 'info',
    running: 'warning',
    finished: 'success',
    failed: 'error',
    cancelled: 'default'
  }
  return types[status] || 'default'
}

const getStatusText = (status) => {
  const texts = {
    created: '已创建',
    waiting: '等待中',
    running: '运行中',
    finished: '已完成',
    failed: '失败',
    cancelled: '已取消'
  }
  return texts[status] || status
}

const getPatternText = (pattern) => {
  const texts = {
    constant: '恒定负载',
    ramp_up: '阶梯增长',
    spike: '尖峰测试',
    stress: '压力测试'
  }
  return texts[pattern] || pattern
}

const formatTime = (time) => {
  if (!time) return '-'
  return new Date(time).toLocaleString('zh-CN')
}

// ============================================================
// 初始化
// ============================================================
onMounted(() => {
  loadTestCases()
  loadTasks()
})
</script>

<style scoped>
.presstest-container {
  max-width: 1400px;
  margin: 0 auto;
}

/* 页面标题 */
.page-header {
  margin-bottom: 24px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px;
  padding: 32px;
  color: white;
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.25);
}

.header-content {
  display: flex;
  align-items: center;
  gap: 20px;
}

.header-icon {
  width: 64px;
  height: 64px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
  backdrop-filter: blur(10px);
}

.header-text {
  flex: 1;
}

.header-title {
  font-size: 28px;
  font-weight: 700;
  margin: 0 0 8px 0;
  text-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.header-subtitle {
  font-size: 14px;
  opacity: 0.95;
  margin: 0;
  font-weight: 400;
}

/* 卡片样式 */
.quick-start-card,
.tasks-card {
  background: white;
  border-radius: 16px;
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  border: 1px solid rgba(0, 0, 0, 0.06);
}

.card-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 18px;
  font-weight: 600;
  color: #333;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 2px solid #f0f0f0;
}

.card-title i {
  color: #667eea;
  font-size: 20px;
}

/* 步骤内容 */
.step-content {
  margin-top: 32px;
  animation: fadeIn 0.3s ease-in-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 600;
  color: #333;
  margin-bottom: 16px;
}

.section-header i {
  color: #667eea;
}

/* 引擎选择 */
.config-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-bottom: 24px;
}

.config-section {
  background: #fafafa;
  border-radius: 12px;
  padding: 20px;
}

.engine-cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.engine-card {
  background: white;
  border: 2px solid #e8e8e8;
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.3s ease;
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
}

.engine-card:hover {
  border-color: #667eea;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
  transform: translateY(-2px);
}

.engine-card.active {
  border-color: #667eea;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.05) 0%, rgba(118, 75, 162, 0.05) 100%);
  box-shadow: 0 4px 16px rgba(102, 126, 234, 0.2);
}

.engine-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 24px;
  flex-shrink: 0;
}

.engine-info {
  flex: 1;
}

.engine-name {
  font-size: 16px;
  font-weight: 600;
  color: #333;
  margin-bottom: 4px;
}

.engine-desc {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.engine-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.engine-check {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #667eea;
  font-size: 20px;
}

/* 确认页面 */
.confirm-section {
  background: #fafafa;
  border-radius: 12px;
  padding: 24px;
}

.confirm-header {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 18px;
  font-weight: 600;
  color: #333;
  margin-bottom: 20px;
}

.confirm-header i {
  color: #667eea;
}

.selected-cases {
  margin-top: 24px;
}

.case-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.case-title {
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

.case-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: #666;
}

.case-priority {
  color: #999;
}

/* 步骤操作按钮 */
.step-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 32px;
  padding-top: 24px;
  border-top: 1px solid #e8e8e8;
}

/* 性能指标卡片 */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.metric-card {
  background: white;
  border-radius: 12px;
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  border: 1px solid rgba(0, 0, 0, 0.04);
}

.metric-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 24px;
  flex-shrink: 0;
}

.metric-content {
  flex: 1;
}

.metric-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 4px;
}

.metric-value {
  font-size: 24px;
  font-weight: 700;
  color: #333;
}

/* 响应式 */
@media (max-width: 1200px) {
  .config-grid {
    grid-template-columns: 1fr;
  }
  
  .metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .metrics-grid {
    grid-template-columns: 1fr;
  }
  
  .header-content {
    flex-direction: column;
    text-align: center;
  }
}
</style>

