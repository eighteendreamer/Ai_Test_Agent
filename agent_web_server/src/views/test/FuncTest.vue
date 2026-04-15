<template>
  <div class="execute-case-view">
    <!-- 页面说明 -->
    <n-card>
      <template #header>
        <div class="flex items-center gap-2">
          <i class="fas fa-robot text-xl text-primary"></i>
          <span class="text-lg font-bold">执行用例 - AI 智能操作</span>
        </div>
      </template>
      <p class="text-gray-500">
        使用 Browser-Use 直接执行测试用例，无需生成代码，LLM 实时决策操作网页。
        支持单条执行或多选批量执行（智能合并共同步骤）。
      </p>
    </n-card>

    <!-- 测试用例列表 -->
    <n-card style="margin-top: 20px">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-bold">测试用例列表</span>
          <n-space>
            <n-button 
              v-if="selectedRowKeys.length > 1"
              type="primary"
              @click="executeBatchCases"
              :loading="isBatchExecuting"
            >
              <template #icon>
                <i class="fas fa-play-circle"></i>
              </template>
              批量执行 ({{ selectedRowKeys.length }} 条)
            </n-button>
            <n-tag v-if="selectedRowKeys.length > 0" type="info">
              已选 {{ selectedRowKeys.length }} 条
            </n-tag>
          </n-space>
        </div>
      </template>

      <n-data-table
        :columns="columns"
        :data="testCases"
        :loading="loading"
        :row-key="row => row.id"
        v-model:checked-row-keys="selectedRowKeys"
        striped
      />

      <!-- 分页 -->
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
    <n-modal v-model:show="dialogVisible" preset="card" title="测试用例详情" style="width: 800px">
      <n-descriptions v-if="currentCase" :column="1" label-placement="left" bordered>
        <n-descriptions-item label="ID">{{ currentCase.id }}</n-descriptions-item>
        <n-descriptions-item label="模块">{{ currentCase.module }}</n-descriptions-item>
        <n-descriptions-item label="用例名称">{{ currentCase.title }}</n-descriptions-item>
        <n-descriptions-item label="前置条件">{{ currentCase.precondition || '无' }}</n-descriptions-item>
        <n-descriptions-item label="测试步骤">
          <div v-for="(step, index) in parseSteps(currentCase.steps)" :key="index" style="margin-bottom: 8px; padding: 6px 8px; background: #f8f8f8; border-radius: 4px;">
            <div><strong>步骤 {{ index + 1 }}：</strong>{{ step.step }}</div>
            <div v-if="step.expected" style="color: #666; margin-top: 2px;">
              <strong>预期：</strong>{{ step.expected }}
            </div>
          </div>
        </n-descriptions-item>
        <n-descriptions-item label="预期结果">{{ currentCase.expected }}</n-descriptions-item>
        <n-descriptions-item label="关键词">{{ currentCase.keywords }}</n-descriptions-item>
        <n-descriptions-item label="优先级">{{ formatPriority(currentCase.priority) }}</n-descriptions-item>
        <n-descriptions-item label="用例类型">{{ currentCase.case_type }}</n-descriptions-item>
        <n-descriptions-item label="适用阶段">{{ currentCase.stage }}</n-descriptions-item>
      </n-descriptions>
    </n-modal>

    <!-- 执行用例对话框 -->
    <n-modal 
      v-model:show="executeDialogVisible" 
      preset="card" 
      :title="isExecuting ? '正在执行' : '执行用例'" 
      style="width: 1000px"
      :mask-closable="!isExecuting"
      :closable="!isExecuting"
    >
      <!-- 用例信息展示 -->
      <n-card v-if="selectedCase" size="small" style="margin-bottom: 20px">
        <template #header>
          <div class="flex items-center justify-between">
            <span><strong>测试用例:</strong> {{ selectedCase.title }}</span>
            <n-tag>{{ selectedCase.case_type }}</n-tag>
          </div>
        </template>
        <n-descriptions :column="1" size="small" label-placement="left" bordered>
          <n-descriptions-item label="模块">{{ selectedCase.module }}</n-descriptions-item>
          <n-descriptions-item label="前置条件">{{ selectedCase.precondition || '无' }}</n-descriptions-item>
          <n-descriptions-item label="测试步骤">
            <div v-for="(step, index) in parseSteps(selectedCase.steps)" :key="index" style="margin: 4px 0; padding: 4px 6px; background: #f8f8f8; border-radius: 4px;">
              <div><strong>{{ index + 1 }}.</strong> {{ step.step }}</div>
              <div v-if="step.expected" style="color: #666; margin-top: 2px; font-size: 12px;">
                <strong>预期：</strong>{{ step.expected }}
              </div>
            </div>
          </n-descriptions-item>
          <n-descriptions-item label="预期结果">{{ selectedCase.expected }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-divider />

      <!-- 执行配置 (未开始执行时显示) -->
      <n-card v-if="!isExecuting && !executionResult" size="small">
        <template #header>
          <strong>执行配置</strong>
        </template>
        <n-form :model="executeConfig" label-placement="left" label-width="120">
          <n-form-item label="无头模式">
            <n-switch v-model:value="executeConfig.headless" />
            <span class="tip">关闭后可看到浏览器操作过程</span>
          </n-form-item>
          
          <n-form-item label="最大步数">
            <n-input-number 
              v-model:value="executeConfig.max_steps" 
              :min="5" 
              :max="100" 
            />
            <span class="tip">防止无限循环</span>
          </n-form-item>
          
          <n-form-item label="视觉能力">
            <n-switch v-model:value="executeConfig.use_vision" />
            <span class="tip">启用后 LLM 可分析截图（成本略高）</span>
          </n-form-item>
        </n-form>
      </n-card>

      <!-- 执行中/执行完成状态显示 -->
      <div v-if="isExecuting || executionResult">
        <n-card size="small">
          <template #header>
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                <i class="fas fa-microchip"></i>
                <strong>代理交互</strong>
                <n-tag v-if="isExecuting && !isPaused" type="info" size="small">执行中...</n-tag>
                <n-tag v-else-if="isPaused" type="warning" size="small">已暂停</n-tag>
                <n-tag 
                  v-else-if="executionResult"
                  :type="executionResult.status === 'pass' ? 'success' : 'error'"
                  size="small"
                >
                  {{ executionResult.status === 'pass' ? '执行成功' : '执行失败' }}
                </n-tag>
              </div>
              <n-space v-if="isExecuting">
                <n-button 
                  v-if="!isPaused"
                  size="small" 
                  type="warning" 
                  @click="pauseExecution"
                  :loading="pauseLoading"
                >
                  暂停执行
                </n-button>
                <n-button 
                  v-else
                  size="small" 
                  type="success" 
                  @click="resumeExecution"
                  :loading="resumeLoading"
                >
                  继续执行
                </n-button>
                <n-button 
                  size="small" 
                  type="error" 
                  @click="stopExecution"
                  :loading="stopLoading"
                >
                  停止执行
                </n-button>
              </n-space>
            </div>
          </template>

          <!-- 执行过程显示 -->
          <div class="agent-output">
            <div v-if="isExecuting" class="executing-status">
              <n-spin size="large" />
              <p class="mt-4">AI 代理正在执行测试...</p>
              <p class="tip">请勿关闭此窗口</p>
            </div>

            <!-- 执行结果 -->
            <div v-else-if="executionResult">
              <n-alert
                :title="executionResult.status === 'pass' ? '✓ 测试执行成功' : '✗ 测试执行失败'"
                :type="executionResult.status === 'pass' ? 'success' : 'error'"
                style="margin-bottom: 20px"
              >
                <div class="mt-2">
                  <p><strong>总步数:</strong> {{ executionResult.total_steps }}</p>
                  <p><strong>耗时:</strong> {{ executionResult.duration }} 秒</p>
                  <p v-if="executionResult.final_url"><strong>最终URL:</strong> {{ executionResult.final_url }}</p>
                  <p v-if="executionResult.error_message" class="text-red-500 mt-2">
                    <strong>错误信息:</strong> {{ executionResult.error_message }}
                  </p>
                </div>
              </n-alert>

              <!-- 执行步骤详情 -->
              <n-collapse v-if="executionResult.history && executionResult.history.steps" accordion>
                <n-collapse-item 
                  v-for="(step, index) in executionResult.history.steps" 
                  :key="index"
                  :title="`步骤 ${step.step_number} - ${step.title || step.url || '执行中'}`"
                  :name="index"
                >
                  <div class="step-detail">
                    <p v-if="step.thinking">
                      <strong>💭 AI 思考:</strong><br/>
                      <span class="thinking-text">{{ step.thinking }}</span>
                    </p>
                    <p v-if="step.url">
                      <strong>🌐 页面:</strong> 
                      <a :href="step.url" target="_blank" class="url-link">{{ step.url }}</a>
                    </p>
                    <p v-if="step.actions && step.actions.length > 0">
                      <strong>⚡ 执行动作:</strong><br/>
                      <n-tag 
                        v-for="(action, idx) in step.actions" 
                        :key="idx"
                        size="small"
                        style="margin: 4px 4px 0 0"
                      >
                        {{ action.action_name }}
                      </n-tag>
                    </p>
                    <p v-if="step.timestamp" class="timestamp">
                      <strong>⏰ 时间:</strong> {{ step.timestamp }}
                    </p>
                  </div>
                </n-collapse-item>
              </n-collapse>
            </div>
          </div>
        </n-card>

        <!-- 任务输出 JSON -->
        <n-card v-if="executionResult" size="small" style="margin-top: 20px">
          <template #header>
            <strong>任务输出</strong>
          </template>
          <n-tabs type="line">
            <n-tab-pane name="history" tab="代理历史 JSON">
              <div class="json-output">
                <pre>{{ JSON.stringify(executionResult.history, null, 2) }}</pre>
              </div>
            </n-tab-pane>
          </n-tabs>
        </n-card>
      </div>

      <!-- 底部按钮 -->
      <template #footer>
        <n-space justify="end">
          <n-button v-if="!isExecuting && !executionResult" @click="executeDialogVisible = false">
            取消
          </n-button>
          <n-button 
            v-if="!isExecuting && !executionResult" 
            type="primary" 
            @click="confirmExecute"
            :loading="isExecuting"
          >
            <template #icon>
              <i class="fas fa-play"></i>
            </template>
            开始执行
          </n-button>
          <n-button v-if="executionResult" @click="executeDialogVisible = false">
            关闭
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 批量执行对话框 -->
    <n-modal 
      v-model:show="batchExecuteDialogVisible" 
      preset="card" 
      :title="isBatchExecuting ? '正在批量执行' : '批量执行用例'" 
      style="width: 1200px"
      :mask-closable="!isBatchExecuting"
      :closable="!isBatchExecuting"
    >
      <!-- 选中的用例列表 -->
      <n-card size="small" style="margin-bottom: 20px">
        <template #header>
          <div class="flex items-center justify-between">
            <span><strong>已选择 {{ selectedBatchCases.length }} 条用例</strong></span>
            <n-tag type="info">智能合并共同步骤执行</n-tag>
          </div>
        </template>
        <n-collapse>
          <n-collapse-item 
            v-for="(tc, index) in selectedBatchCases" 
            :key="tc.id"
            :title="`用例 ${index + 1}: ${tc.title}`"
            :name="tc.id"
          >
            <n-descriptions :column="1" size="small" label-placement="left" bordered>
              <n-descriptions-item label="模块">{{ tc.module }}</n-descriptions-item>
              <n-descriptions-item label="前置条件">{{ tc.precondition || '无' }}</n-descriptions-item>
              <n-descriptions-item label="测试步骤">
                <div v-for="(step, idx) in parseSteps(tc.steps)" :key="idx" style="margin: 4px 0; padding: 4px 6px; background: #f8f8f8; border-radius: 4px;">
                  <div><strong>{{ idx + 1 }}.</strong> {{ step.step }}</div>
                  <div v-if="step.expected" style="color: #666; margin-top: 2px; font-size: 12px;">
                    <strong>预期：</strong>{{ step.expected }}
                  </div>
                </div>
              </n-descriptions-item>
              <n-descriptions-item label="预期结果">{{ tc.expected }}</n-descriptions-item>
            </n-descriptions>
          </n-collapse-item>
        </n-collapse>
      </n-card>

      <n-divider />

      <!-- 执行配置 -->
      <n-card v-if="!isBatchExecuting && !batchExecutionResult" size="small">
        <template #header>
          <strong>执行配置</strong>
        </template>
        <n-form :model="batchExecuteConfig" label-placement="left" label-width="120">
          <n-form-item label="无头模式">
            <n-switch v-model:value="batchExecuteConfig.headless" />
            <span class="tip">关闭后可看到浏览器操作过程</span>
          </n-form-item>
          
          <n-form-item label="最大步数">
            <n-input-number 
              v-model:value="batchExecuteConfig.max_steps" 
              :min="10" 
              :max="200" 
            />
            <span class="tip">批量执行建议设置较大值</span>
          </n-form-item>
          
          <n-form-item label="视觉能力">
            <n-switch v-model:value="batchExecuteConfig.use_vision" />
            <span class="tip">启用后 LLM 可分析截图</span>
          </n-form-item>
        </n-form>
      </n-card>

      <!-- 执行中/执行完成状态显示 -->
      <div v-if="isBatchExecuting || batchExecutionResult">
        <n-card size="small">
          <template #header>
            <div class="flex items-center gap-2">
              <i class="fas fa-microchip"></i>
              <strong>批量执行状态</strong>
              <n-tag v-if="isBatchExecuting" type="info" size="small">执行中...</n-tag>
              <n-tag 
                v-else-if="batchExecutionResult"
                :type="batchExecutionResult.summary && batchExecutionResult.summary.failed === 0 ? 'success' : (batchExecutionResult.summary && batchExecutionResult.summary.passed > 0 ? 'warning' : 'error')"
                size="small"
              >
                {{ batchExecutionResult.summary ? `${batchExecutionResult.summary.passed} 通过 / ${batchExecutionResult.summary.failed} 失败` : (batchExecutionResult.status === 'pass' ? '执行成功' : '执行失败') }}
              </n-tag>
            </div>
          </template>

          <div class="agent-output">
            <div v-if="isBatchExecuting" class="executing-status">
              <n-spin size="large" />
              <p class="mt-4">AI 代理正在批量执行测试...</p>
              <p class="tip">正在智能分析并合并共同步骤</p>
            </div>

            <div v-else-if="batchExecutionResult">
              <!-- 总体摘要 -->
              <n-alert
                :title="batchExecutionResult.summary && batchExecutionResult.summary.failed === 0 ? '✓ 批量测试全部通过' : '⚠ 批量测试执行完成'"
                :type="batchExecutionResult.summary && batchExecutionResult.summary.failed === 0 ? 'success' : 'warning'"
                style="margin-bottom: 20px"
              >
                <div class="mt-2">
                  <p><strong>总步数:</strong> {{ batchExecutionResult.total_steps }}</p>
                  <p><strong>耗时:</strong> {{ batchExecutionResult.duration }} 秒</p>
                  <p v-if="batchExecutionResult.summary">
                    <strong>通过:</strong> {{ batchExecutionResult.summary.passed }} 条 | 
                    <strong>失败:</strong> {{ batchExecutionResult.summary.failed }} 条 | 
                    <strong>总计:</strong> {{ batchExecutionResult.summary.total }} 条
                  </p>
                </div>
              </n-alert>

              <!-- 每条用例的独立结果 -->
              <n-collapse v-if="batchExecutionResult.raw_results" style="margin-bottom: 20px">
                <n-collapse-item 
                  v-for="(caseResult, idx) in batchExecutionResult.raw_results" 
                  :key="idx"
                  :name="idx"
                >
                  <template #header>
                    <div class="flex items-center gap-2">
                      <n-tag :type="caseResult.data && caseResult.data.status === 'pass' ? 'success' : 'error'" size="small">
                        {{ caseResult.data && caseResult.data.status === 'pass' ? '通过' : '失败' }}
                      </n-tag>
                      <span>用例 {{ idx + 1 }} - {{ caseResult.data && caseResult.data.duration ? caseResult.data.duration + '秒' : '' }}</span>
                    </div>
                  </template>
                  <div v-if="caseResult.data">
                    <p><strong>步数:</strong> {{ caseResult.data.total_steps }}</p>
                    <p><strong>耗时:</strong> {{ caseResult.data.duration }} 秒</p>
                    <p v-if="caseResult.data.history && caseResult.data.history.final_state">
                      <strong>最终URL:</strong> {{ caseResult.data.history.final_state.url }}
                    </p>
                    <!-- 用例步骤详情 -->
                    <n-collapse v-if="caseResult.data.history && caseResult.data.history.steps" style="margin-top: 10px">
                      <n-collapse-item 
                        v-for="(step, sIdx) in caseResult.data.history.steps" 
                        :key="sIdx"
                        :title="`步骤 ${step.step_number} - ${step.title || step.url || '执行中'}`"
                        :name="sIdx"
                      >
                        <div class="step-detail">
                          <p v-if="step.thinking">
                            <strong>💭 AI 思考:</strong><br/>
                            <span class="thinking-text">{{ step.thinking }}</span>
                          </p>
                          <p v-if="step.url">
                            <strong>🌐 页面:</strong> 
                            <a :href="step.url" target="_blank" class="url-link">{{ step.url }}</a>
                          </p>
                          <div  v-if="step.actions && step.actions.length > 0">
                            <strong>⚡ 执行动作:</strong>
                            <pre style="font-size: 12px; margin-top: 4px;">{{ JSON.stringify(step.actions, null, 2) }}</pre>
                          </div >
                        </div>
                      </n-collapse-item>
                    </n-collapse>
                  </div>
                </n-collapse-item>
              </n-collapse>

              <!-- 合并步骤详情（兼容旧格式） -->
              <n-collapse v-else-if="batchExecutionResult.history && batchExecutionResult.history.steps" accordion>
                <n-collapse-item 
                  v-for="(step, index) in batchExecutionResult.history.steps" 
                  :key="index"
                  :title="`步骤 ${step.step_number} - ${step.title || step.url || '执行中'}`"
                  :name="index"
                >
                  <div class="step-detail">
                    <p v-if="step.thinking">
                      <strong>💭 AI 思考:</strong><br/>
                      <span class="thinking-text">{{ step.thinking }}</span>
                    </p>
                    <p v-if="step.memory">
                      <strong>📝 记忆:</strong><br/>
                      <span class="memory-text">{{ step.memory }}</span>
                    </p>
                    <p v-if="step.url">
                      <strong>🌐 页面:</strong> 
                      <a :href="step.url" target="_blank" class="url-link">{{ step.url }}</a>
                    </p>
                    <p v-if="step.actions && step.actions.length > 0">
                      <strong>⚡ 执行动作:</strong><br/>
                      <n-tag 
                        v-for="(action, idx) in step.actions" 
                        :key="idx"
                        size="small"
                        style="margin: 4px 4px 0 0"
                      >
                        {{ action.action_name }}
                      </n-tag>
                    </p>
                  </div>
                </n-collapse-item>
              </n-collapse>
            </div>
          </div>
        </n-card>

        <!-- 任务输出 JSON -->
        <n-card v-if="batchExecutionResult" size="small" style="margin-top: 20px">
          <template #header>
            <strong>任务输出</strong>
          </template>
          <n-tabs type="line">
            <n-tab-pane name="history" tab="代理历史 JSON">
              <div class="json-output">
                <pre>{{ JSON.stringify(batchExecutionResult.history, null, 2) }}</pre>
              </div>
            </n-tab-pane>
          </n-tabs>
        </n-card>
      </div>

      <!-- 底部按钮 -->
      <template #footer>
        <n-space justify="end">
          <n-button v-if="!isBatchExecuting && !batchExecutionResult" @click="batchExecuteDialogVisible = false">
            取消
          </n-button>
          <n-button 
            v-if="!isBatchExecuting && !batchExecutionResult" 
            type="primary" 
            @click="confirmBatchExecute"
            :loading="isBatchExecuting"
          >
            <template #icon>
              <i class="fas fa-play-circle"></i>
            </template>
            开始批量执行
          </n-button>
          <n-button v-if="batchExecutionResult" @click="closeBatchDialog">
            关闭
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, h, reactive, onMounted } from 'vue'
import { 
  NCard, NButton, NDataTable, NModal, NDescriptions, NDescriptionsItem, 
  NTag, NForm, NFormItem, NSwitch, NInputNumber, NSpace, NDivider,
  NAlert, NCollapse, NCollapseItem, NTabs, NTabPane, NSpin, NPagination,
  useMessage, useDialog
} from 'naive-ui'
import { testCaseAPI, testCodeAPI } from '@/api'
import { useLazyLoad } from '@/composables/useLazyLoad'
import { parseSteps } from '@/utils/stepsUtils'

const message = useMessage()
const dialog = useDialog()

// 筛选条件
const filters = reactive({
  case_type: '功能测试',
  project_id: parseInt(localStorage.getItem('currentProjectId')) || null
})

// 使用懒加载
const {
  data: testCases,
  loading,
  currentPage,
  pageSize,
  total,
  refresh,
  goToPage,
  changePageSize
} = useLazyLoad({
  fetchFunction: testCaseAPI.getList,
  pageSize: 10,
  filters,
  autoLoad: true
})

const dialogVisible = ref(false)
const currentCase = ref(null)
const executingCases = ref({})
const executeDialogVisible = ref(false)
const selectedCase = ref(null)
const isExecuting = ref(false)
const executionResult = ref(null)
const isPaused = ref(false)
const pauseLoading = ref(false)
const resumeLoading = ref(false)
const stopLoading = ref(false)
const currentTaskId = ref(null)

// 批量执行相关
const selectedRowKeys = ref([])
const isBatchExecuting = ref(false)
const batchExecuteDialogVisible = ref(false)
const selectedBatchCases = ref([])
const batchExecutionResult = ref(null)

const executeConfig = reactive({
  headless: true,
  max_steps: 20,
  use_vision: false
})

const batchExecuteConfig = reactive({
  headless: true,
  max_steps: 50,
  use_vision: false
})

// 优先级类型映射
const getPriorityType = (priority) => {
  const typeMap = { '1': 'error', '2': 'warning', '3': 'info', '4': 'default' }
  return typeMap[String(priority)] || 'default'
}

// 格式化优先级
const formatPriority = (priority) => {
  if (/^[1-4]$/.test(String(priority))) {
    return `${priority}级`
  }
  return priority
}

// 表格列定义
const columns = [
  { type: 'selection' },
  { title: 'ID', key: 'id', width: 80 },
  { title: '模块', key: 'module', width: 120 },
  { title: '用例名称', key: 'title', width: 200, ellipsis: { tooltip: true } },
  { 
    title: '步骤', 
    key: 'steps',
    render(row) {
      const steps = parseSteps(row.steps)
      if (!steps.length) return '-'
      return h('div', {}, steps.slice(0, 3).map((s, i) =>
        h('div', { style: 'margin: 2px 0; font-size: 12px;' }, `${i + 1}. ${s.step}`)
      ).concat(steps.length > 3 ? [h('div', { style: 'font-size: 12px; color: #999;' }, `...共${steps.length}步`)] : []))
    }
  },
  { 
    title: '优先级', 
    key: 'priority', 
    width: 100,
    render(row) {
      return h(NTag, { type: getPriorityType(row.priority), size: 'small' }, 
        { default: () => formatPriority(row.priority) }
      )
    }
  },
  { title: '用例类型', key: 'case_type', width: 100 },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    fixed: 'right',
    render(row) {
      return h(NSpace, {}, {
        default: () => [
          h(NButton, { size: 'small', type: 'primary', onClick: () => viewDetail(row) }, 
            { default: () => '查看详情' }
          ),
          h(NButton, { 
            size: 'small', 
            type: 'success', 
            onClick: () => executeCase(row),
            loading: executingCases.value[row.id]
          }, { 
            default: () => '🤖 执行用例' 
          })
        ]
      })
    }
  }
]

// 查看详情
const viewDetail = (row) => {
  currentCase.value = row
  dialogVisible.value = true
}

// 执行用例 - 显示执行对话框
const executeCase = (testCase) => {
  selectedCase.value = testCase
  executionResult.value = null
  isExecuting.value = false
  executeDialogVisible.value = true
}

// 暂停执行
const pauseExecution = async () => {
  if (!currentTaskId.value) return
  
  pauseLoading.value = true
  try {
    const result = await testCodeAPI.pauseTask(currentTaskId.value)
    if (result.success) {
      isPaused.value = true
      message.success('已暂停执行')
    }
  } catch (error) {
    message.error('暂停失败: ' + (error.message || '未知错误'))
  } finally {
    pauseLoading.value = false
  }
}

// 恢复执行
const resumeExecution = async () => {
  if (!currentTaskId.value) return
  
  resumeLoading.value = true
  try {
    const result = await testCodeAPI.resumeTask(currentTaskId.value)
    if (result.success) {
      isPaused.value = false
      message.success('已恢复执行')
    }
  } catch (error) {
    message.error('恢复失败: ' + (error.message || '未知错误'))
  } finally {
    resumeLoading.value = false
  }
}

// 停止执行
const stopExecution = async () => {
  if (!currentTaskId.value) return
  
  dialog.warning({
    title: '确认停止',
    content: '确定要停止当前测试吗？停止后无法恢复。',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      stopLoading.value = true
      try {
        const result = await testCodeAPI.stopTask(currentTaskId.value)
        if (result.success) {
          isExecuting.value = false
          isPaused.value = false
          message.success('已停止执行')
          
          executionResult.value = {
            status: 'fail',
            error_message: '用户手动停止',
            total_steps: 0,
            duration: 0,
            history: null
          }
        }
      } catch (error) {
        message.error('停止失败: ' + (error.message || '未知错误'))
      } finally {
        stopLoading.value = false
      }
    }
  })
}

// 确认执行
const confirmExecute = async () => {
  const caseId = selectedCase.value.id
  
  isExecuting.value = true
  isPaused.value = false
  currentTaskId.value = caseId
  executingCases.value[caseId] = true
  
  try {
    message.info('🤖 AI 正在接管浏览器执行测试...')
    
    const result = await testCodeAPI.executeBrowserUse(
      caseId,
      executeConfig.headless,
      executeConfig.max_steps,
      executeConfig.use_vision
    )
    
    if (result.success) {
      message.success('执行完成！')
      executionResult.value = result.data
      // 同步服务端返回的真实 task_id（覆盖临时值）
      if (result.data?.task_id) {
        currentTaskId.value = result.data.task_id
      }
    } else {
      message.error(result.message || '执行失败')
      
      executionResult.value = {
        status: 'fail',
        error_message: result.message,
        total_steps: 0,
        duration: 0,
        history: null
      }
    }
  } catch (error) {
    message.error('执行失败: ' + (error.message || '未知错误'))
    console.error(error)
    
    executionResult.value = {
      status: 'fail',
      error_message: error.message || '未知错误',
      total_steps: 0,
      duration: 0,
      history: null
    }
  } finally {
    isExecuting.value = false
    isPaused.value = false
    currentTaskId.value = null
    executingCases.value[caseId] = false
  }
}

// 批量执行用例
const executeBatchCases = async () => {
  if (selectedRowKeys.value.length < 2) {
    message.warning('请至少选择2条用例进行批量执行')
    return
  }
  
  // 从后端获取选中的所有用例详情（支持跨页选择）
  try {
    loading.value = true
    const promises = selectedRowKeys.value.map(id => testCaseAPI.getById(id))
    const results = await Promise.all(promises)
    selectedBatchCases.value = results.map(res => res.data || res)
    batchExecutionResult.value = null
    isBatchExecuting.value = false
    batchExecuteDialogVisible.value = true
  } catch (error) {
    message.error('获取用例详情失败')
    console.error(error)
  } finally {
    loading.value = false
  }
}

// 确认批量执行
const confirmBatchExecute = async () => {
  const caseIds = selectedRowKeys.value
  
  isBatchExecuting.value = true
  
  try {
    message.info(`🤖 AI 正在批量执行 ${caseIds.length} 条测试用例...`)
    
    const result = await testCodeAPI.executeBatchBrowserUse(
      caseIds,
      batchExecuteConfig.headless,
      batchExecuteConfig.max_steps,
      batchExecuteConfig.use_vision
    )
    
    if (result.success) {
      message.success('批量执行完成！')
      
      // 适配批量执行返回的数据结构
      const data = result.data || result
      if (data.results && data.summary) {
        // 批量执行返回格式，转换为前端模板可渲染的格式
        const summary = data.summary
        const allSteps = []
        let totalSteps = 0
        let totalDuration = 0
        let finalUrl = ''
        
        data.results.forEach((r, idx) => {
          const d = r.data || {}
          totalSteps += d.total_steps || 0
          totalDuration += d.duration || 0
          if (d.history && d.history.steps) {
            d.history.steps.forEach(step => {
              allSteps.push({
                ...step,
                title: `[用例${idx + 1}] ${step.title || step.url || '执行中'}`
              })
            })
          }
          if (d.history && d.history.final_state) {
            finalUrl = d.history.final_state.url || finalUrl
          }
        })
        
        batchExecutionResult.value = {
          status: summary.failed === 0 ? 'pass' : 'fail',
          total_steps: totalSteps,
          duration: totalDuration,
          final_url: finalUrl,
          error_message: summary.failed > 0 ? `${summary.failed} 条用例执行失败` : '',
          history: {
            total_steps: totalSteps,
            steps: allSteps
          },
          summary: summary,
          raw_results: data.results
        }
      } else {
        // 兼容其他返回格式
        batchExecutionResult.value = data
      }
    } else {
      message.error(result.message || '批量执行失败')
      
      batchExecutionResult.value = {
        status: 'fail',
        error_message: result.message,
        total_steps: 0,
        duration: 0,
        history: null
      }
    }
  } catch (error) {
    message.error('批量执行失败: ' + (error.message || '未知错误'))
    console.error(error)
    
    batchExecutionResult.value = {
      status: 'fail',
      error_message: error.message || '未知错误',
      total_steps: 0,
      duration: 0,
      history: null
    }
  } finally {
    isBatchExecuting.value = false
  }
}

// 关闭批量执行对话框
const closeBatchDialog = () => {
  batchExecuteDialogVisible.value = false
  selectedRowKeys.value = []
}

// 注：useLazyLoad 已经设置 autoLoad: true，不需要 onMounted 手动加载
</script>

<style scoped>
.execute-case-view {
  padding: 0;
}

.text-primary {
  color: #007857;
}

.tip {
  margin-left: 10px;
  font-size: 12px;
  color: #909399;
}

.agent-output {
  min-height: 200px;
}

.executing-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px;
  color: #606266;
}

.step-detail {
  padding: 12px;
  background-color: #f5f7fa;
  border-radius: 4px;
}

.step-detail p {
  margin: 8px 0;
  line-height: 1.6;
}

.thinking-text {
  color: #606266;
  background-color: #fff;
  padding: 8px 12px;
  border-radius: 4px;
  display: block;
  margin-top: 4px;
  border-left: 3px solid #007857;
}

.memory-text {
  color: #409eff;
  background-color: #ecf5ff;
  padding: 8px 12px;
  border-radius: 4px;
  display: block;
  margin-top: 4px;
  border-left: 3px solid #409eff;
}

.url-link {
  color: #007857;
  text-decoration: none;
}

.url-link:hover {
  text-decoration: underline;
}

.timestamp {
  font-size: 12px;
  color: #909399;
}

.json-output {
  max-height: 400px;
  overflow: auto;
  background-color: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
}

.json-output pre {
  margin: 0;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
  color: #303133;
}
</style>
