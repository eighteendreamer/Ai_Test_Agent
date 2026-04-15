<template>
  <div class="mixed-report-container">
    <n-spin :show="loading" description="加载数据源中...">
      <div class="grid grid-cols-2 gap-8">
        <!-- 左侧：数据源选择 -->
        <div class="glass-card p-6">
          <h4 class="font-bold mb-4">第一步：选择数据源</h4>
          <div v-if="reportList.length === 0" class="text-center py-8 text-gray-400">
            <i class="fas fa-inbox text-4xl mb-2"></i>
            <p>暂无可用数据源</p>
          </div>
          <div v-else class="space-y-4 h-[350px] overflow-y-auto pr-2 custom-scrollbar">
            <n-checkbox-group v-model:value="selectedReports">
              <div class="space-y-4">
                <label
                  v-for="report in reportList"
                  :key="report.id"
                  class="flex items-center justify-between p-4 border rounded-xl hover:bg-slate-50 cursor-pointer transition-all"
                >
                  <div class="flex items-center gap-3">
                    <i :class="['fas', report.icon, report.iconColor]"></i>
                    <div>
                      <p class="text-sm font-bold">{{ report.title }}</p>
                      <p class="text-xs text-slate-400">{{ report.description }}</p>
                    </div>
                  </div>
                  <n-checkbox :value="report.id" />
                </label>
              </div>
            </n-checkbox-group>
          </div>
        </div>

      <!-- 右侧：生成按钮 -->
      <div class="glass-card p-10 flex flex-col items-center justify-center text-center">
        <div class="w-20 h-20 bg-[#007857]/5 text-[#007857] rounded-full flex items-center justify-center text-4xl mb-4">
          <i class="fas fa-layer-group"></i>
        </div>
        <h4 class="text-lg font-bold">生成综合评估报告</h4>
        <p class="text-xs text-slate-400 mt-2 mb-8 max-w-xs">
          系统将自动整合 Bug 列表与运行数据，并利用大模型生成结论性总结摘要。
        </p>
        <n-button 
          type="primary" 
          size="large" 
          block
          :loading="generating"
          :disabled="selectedReports.length === 0"
          @click="generateReport"
        >
          <template #icon>
            <i class="fas fa-wand-magic-sparkles"></i>
          </template>
          开始聚合处理
        </n-button>
      </div>
    </div>
    </n-spin>

    <!-- 生成结果预览 -->
    <div v-if="generatedReport" class="glass-card p-6 mt-8">
    <div class="flex justify-between items-center mb-4">
      <h4 class="font-bold">综合评估报告</h4>
      <div class="flex gap-2">
        <n-button type="primary" @click="showSendModal = true">
          <template #icon>
            <i class="fas fa-paper-plane"></i>
          </template>
          发送给联系人
        </n-button>
        <n-button @click="downloadReport('pdf')">
          <template #icon>
            <i class="fas fa-file-pdf"></i>
          </template>
          导出 PDF
        </n-button>
        <n-button @click="downloadReport('html')">
          <template #icon>
            <i class="fas fa-file-code"></i>
          </template>
          导出 HTML
        </n-button>
      </div>
    </div>
    
    <n-divider />
    
    <div class="prose max-w-none">
      <h5 class="text-lg font-bold mb-2">测试概要</h5>
      <p class="text-slate-600 mb-4">{{ generatedReport.summary }}</p>
      
      <div class="grid grid-cols-4 gap-4 mb-6">
        <div class="p-4 bg-purple-50 rounded-lg text-center">
          <p class="text-2xl font-bold text-purple-600">{{ generatedReport.quality_rating }}</p>
          <p class="text-xs text-slate-500">质量评级</p>
        </div>
        <div class="p-4 bg-green-50 rounded-lg text-center">
          <p class="text-2xl font-bold text-green-600">{{ generatedReport.pass_rate || generatedReport.passRate }}%</p>
          <p class="text-xs text-slate-500">通过率</p>
        </div>
        <div class="p-4 bg-red-50 rounded-lg text-center">
          <p class="text-2xl font-bold text-red-600">{{ generatedReport.bug_count || generatedReport.bugCount }}</p>
          <p class="text-xs text-slate-500">Bug 数量</p>
        </div>
        <div class="p-4 bg-blue-50 rounded-lg text-center">
          <p class="text-2xl font-bold text-blue-600">{{ generatedReport.duration }}</p>
          <p class="text-xs text-slate-500">执行时长</p>
        </div>
      </div>
      
      <h5 class="text-lg font-bold mb-2">AI 分析结论</h5>
      <div class="markdown-body" v-html="conclusionHtml"></div>
    </div>
    </div>

    <!-- 发送报告模态框 -->
    <n-modal v-model:show="showSendModal">
    <n-card
      style="width: 600px"
      title="发送综合评估报告"
      :bordered="false"
      size="huge"
      role="dialog"
      aria-modal="true"
    >
      <template #header-extra>
        <n-button text @click="showSendModal = false">
          <template #icon>
            <i class="fas fa-times"></i>
          </template>
        </n-button>
      </template>
      
      <div class="mb-4">
        <p class="text-slate-600 text-sm mb-3">请选择要接收此报告的联系人：</p>
        <n-spin :show="loadingContacts">
          <n-checkbox-group v-model:value="selectedContacts">
            <div class="space-y-2">
              <div 
                v-for="contact in contacts" 
                :key="contact.id"
                class="flex items-center gap-2 p-2 hover:bg-slate-50 rounded"
              >
                <n-checkbox :value="contact.id">
                  <div class="flex items-center gap-2">
                    <i class="fas fa-user text-slate-400"></i>
                    <span class="font-medium">{{ contact.name }}</span>
                    <span class="text-xs text-slate-400">({{ contact.email }})</span>
                  </div>
                </n-checkbox>
              </div>
              <div v-if="contacts.length === 0" class="text-center py-4 text-slate-400">
                <i class="fas fa-inbox text-2xl mb-2"></i>
                <p>暂无联系人</p>
              </div>
            </div>
          </n-checkbox-group>
        </n-spin>
      </div>

      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="showSendModal = false">取消</n-button>
          <n-button 
            type="primary" 
            :loading="sending"
            :disabled="selectedContacts.length === 0"
            @click="sendReport"
          >
            <template #icon>
              <i class="fas fa-paper-plane"></i>
            </template>
            发送给 {{ selectedContacts.length }} 人
          </n-button>
        </div>
      </template>
    </n-card>
  </n-modal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { NCheckboxGroup, NCheckbox, NButton, NDivider, useMessage, NSpin, NModal, NCard } from 'naive-ui'
import { testReportAPI, bugReportAPI, contactAPI } from '@/api/index'
import { marked } from 'marked'

const message = useMessage()
const generating = ref(false)
const loading = ref(false)
const selectedReports = ref([])
const generatedReport = ref(null)
const reportList = ref([])

// markdown 渲染
const conclusionHtml = computed(() => {
  if (!generatedReport.value?.conclusion) return ''
  return marked(generatedReport.value.conclusion)
})

// 联系人相关
const showSendModal = ref(false)
const contacts = ref([])
const selectedContacts = ref([])
const loadingContacts = ref(false)
const sending = ref(false)

// 加载数据源列表
const loadDataSources = async () => {
  loading.value = true
  try {
    // 获取当前选中的项目ID
    const projectId = parseInt(localStorage.getItem('currentProjectId')) || null
    
    // 并行加载运行测试报告和 Bug 报告
    const [testReportsRes, bugsRes] = await Promise.all([
      testReportAPI.getList({ 
        limit: 50, 
        offset: 0,
        project_id: projectId  // 添加项目ID过滤
      }),
      bugReportAPI.getList({ 
        limit: 100, 
        offset: 0,
        project_id: projectId  // 添加项目ID过滤
      })
    ])

    const dataSources = []

    // 处理运行测试报告
    if (testReportsRes.success && testReportsRes.data) {
      testReportsRes.data.forEach(report => {
        let summary = {}
        try {
          summary = typeof report.summary === 'string' ? JSON.parse(report.summary) : report.summary
        } catch (e) {
          console.error('解析 summary 失败:', e)
        }

        // 计算通过率
        const total = summary.total || 0
        const passed = summary.pass || 0
        const passRate = total > 0 ? Math.round((passed / total) * 100) : 0

        // 格式化执行时长
        const duration = summary.duration || report.duration || 0
        const durationText = duration >= 60 
          ? `${Math.floor(duration / 60)}min ${duration % 60}s`
          : `${duration}s`

        dataSources.push({
          id: `run-${report.id}`,
          reportId: report.id,
          type: 'run',
          title: `运行测试报告 #${report.id}`,
          description: `执行时长: ${durationText} | 通过率 ${passRate}%`,
          icon: 'fa-running',
          iconColor: 'text-blue-500',
          data: report
        })
      })
    }

    // 处理 Bug 报告（按严重程度分组统计）
    if (bugsRes.success && bugsRes.data) {
      const bugs = bugsRes.data
      const severityCounts = {
        '一级': 0,
        '二级': 0,
        '三级': 0,
        '四级': 0
      }

      bugs.forEach(bug => {
        if (severityCounts.hasOwnProperty(bug.severity_level)) {
          severityCounts[bug.severity_level]++
        }
      })

      if (bugs.length > 0) {
        const severityText = Object.entries(severityCounts)
          .filter(([_, count]) => count > 0)
          .map(([level, count]) => `${level}:${count}个`)
          .join(', ')

        dataSources.push({
          id: 'bug-all',
          type: 'bug',
          title: `Bug 测试报告 (共${bugs.length}个)`,
          description: severityText || `包含 ${bugs.length} 个待处理缺陷`,
          icon: 'fa-bug',
          iconColor: 'text-red-500',
          data: bugs
        })
      }
    }

    reportList.value = dataSources

  } catch (error) {
    console.error('加载数据源失败:', error)
    message.error('加载数据源失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  // 分别加载，避免互相影响
  try {
    await loadDataSources()
  } catch (error) {
    console.error('初始化数据源失败:', error)
  }
  
  try {
    await loadContacts()
  } catch (error) {
    console.error('初始化联系人失败:', error)
  }
})

// 组件卸载时清理状态，防止影响其他页面
onBeforeUnmount(() => {
  loading.value = false
  generating.value = false
  loadingContacts.value = false
  sending.value = false
})

// 加载联系人列表
const loadContacts = async () => {
  loadingContacts.value = true
  try {
    const res = await contactAPI.getList()
    // 后端直接返回数组，不是 {code, data} 格式
    contacts.value = Array.isArray(res) ? res : (res.data || [])
  } catch (error) {
    console.error('加载联系人失败:', error)
    // 不显示错误提示，避免影响主要功能
    contacts.value = []
  } finally {
    loadingContacts.value = false
  }
}

const generateReport = async () => {
  if (selectedReports.value.length === 0) {
    message.warning('请至少选择一个数据源')
    return
  }

  generating.value = true
  
  try {
    // 获取选中的数据源
    const selectedSources = reportList.value.filter(r => selectedReports.value.includes(r.id))
    
    // 分离运行测试报告和 Bug 报告
    const runReportIds = selectedSources
      .filter(s => s.type === 'run')
      .map(s => s.reportId)
    
    const bugReportIds = selectedSources
      .filter(s => s.type === 'bug')
      .length > 0 ? [] : []  // 如果选了 Bug，传空数组表示使用所有 Bug

    if (runReportIds.length === 0) {
      message.warning('请至少选择一个运行测试报告')
      generating.value = false
      return
    }

    // 调用后端 LLM 分析 API
    message.info('正在使用 AI 分析报告数据...')
    const result = await testReportAPI.generateMixed(runReportIds, bugReportIds)
    
    if (result.success) {
      const data = result.data
      const s = data.summary || {}
      const passRate = s.pass_rate ?? (s.total > 0 ? Math.round(s.pass / s.total * 100) : 0)
      
      // 根据通过率推导质量评级
      let qualityRating = 'D'
      if (passRate >= 95) qualityRating = 'A'
      else if (passRate >= 80) qualityRating = 'B'
      else if (passRate >= 60) qualityRating = 'C'

      // 计算总执行时长
      const duration = s.duration || 0
      const durationText = duration >= 60
        ? `${Math.floor(duration / 60)}min ${duration % 60}s`
        : `${duration}s`

      generatedReport.value = {
        summary: `共 ${s.total || 0} 个用例，通过 ${s.pass || 0} 个，失败 ${s.fail || 0} 个，通过率 ${passRate}%`,
        quality_rating: qualityRating,
        pass_rate: passRate,
        bug_count: s.bug_count || 0,
        duration: durationText,
        conclusion: data.content || '暂无分析结论',
        raw: data
      }
      message.success('AI 分析完成')
    } else {
      message.error(result.message || '生成报告失败')
    }
    
  } catch (error) {
    console.error('生成报告失败:', error)
    message.error('生成报告失败: ' + (error.message || '未知错误'))
  } finally {
    generating.value = false
  }
}

const downloadReport = (format) => {
  message.info(`导出 ${format.toUpperCase()} 格式功能开发中...`)
}

// 发送报告给联系人
const sendReport = async () => {
  if (selectedContacts.value.length === 0) {
    message.warning('请选择至少一位联系人')
    return
  }
  
  sending.value = true
  try {
    const res = await testReportAPI.sendReport(generatedReport.value, selectedContacts.value)
    
    if (res.code === 200) {
      message.success(res.message || '报告发送成功')
      showSendModal.value = false
      selectedContacts.value = []
    } else {
      message.error(res.message || '发送失败')
    }
  } catch (error) {
    console.error('发送报告失败:', error)
    message.error('发送报告失败: ' + (error.message || '未知错误'))
  } finally {
    sending.value = false
  }
}

</script>

<style scoped>
.glass-card {
  background: white;
  border: 1px solid rgba(0, 120, 87, 0.1);
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
}

.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: #cbd5e1;
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: #94a3b8;
}

.markdown-body {
  color: #334155;
  line-height: 1.8;
}
.markdown-body :deep(h1) {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 1.2em 0 0.6em;
  color: #1e293b;
}
.markdown-body :deep(h2) {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 1em 0 0.5em;
  color: #1e293b;
}
.markdown-body :deep(h3) {
  font-size: 1.1rem;
  font-weight: 600;
  margin: 0.8em 0 0.4em;
  color: #334155;
}
.markdown-body :deep(p) {
  margin: 0.5em 0;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.5em;
  margin: 0.5em 0;
}
.markdown-body :deep(li) {
  margin: 0.25em 0;
}
.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.8em 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #e2e8f0;
  padding: 8px 12px;
  text-align: left;
}
.markdown-body :deep(th) {
  background: #f8fafc;
  font-weight: 600;
}
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 1em 0;
}
.markdown-body :deep(strong) {
  font-weight: 600;
  color: #1e293b;
}
.markdown-body :deep(code) {
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
}
.markdown-body :deep(blockquote) {
  border-left: 3px solid #007857;
  padding-left: 1em;
  margin: 0.5em 0;
  color: #64748b;
}
</style>
