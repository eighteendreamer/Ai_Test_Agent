<template>
  <div class="dashboard-container">
    <!-- 顶部统计卡片 -->
    <n-grid x-gap="12" cols="2 s:3 m:4 l:5" responsive="screen" style="margin-bottom: 12px">
      <n-gi>
        <div class="stat-card test-cases">
          <div class="stat-icon"><i class="fas fa-list-check"></i></div>
          <div class="stat-content">
            <div class="stat-value">{{ stats.testCases }}</div>
            <div class="stat-label">测试用例总数</div>
          </div>
        </div>
      </n-gi>
      <n-gi>
        <div class="stat-card test-reports">
          <div class="stat-icon"><i class="fas fa-file-lines"></i></div>
          <div class="stat-content">
            <div class="stat-value">{{ stats.testReports }}</div>
            <div class="stat-label">测试报告数</div>
          </div>
        </div>
      </n-gi>
      <n-gi>
        <div class="stat-card bugs">
          <div class="stat-icon"><i class="fas fa-bug"></i></div>
          <div class="stat-content">
            <div class="stat-value">{{ stats.bugReports }}</div>
            <div class="stat-label">Bug报告数</div>
          </div>
        </div>
      </n-gi>
      <n-gi>
        <div class="stat-card emails">
          <div class="stat-icon"><i class="fas fa-envelope"></i></div>
          <div class="stat-content">
            <div class="stat-value">{{ stats.emailsSent }}</div>
            <div class="stat-label">邮件发送数</div>
          </div>
        </div>
      </n-gi>
      <n-gi>
        <div class="stat-card security">
          <div class="stat-icon"><i class="fas fa-shield-halved"></i></div>
          <div class="stat-content">
            <div class="stat-value">{{ securityStats.scan_summary?.total || 0 }}</div>
            <div class="stat-label">安全扫描数</div>
          </div>
        </div>
      </n-gi>
    </n-grid>

    <!-- 第一行：测试结果 + 优先级 + 用例类型 -->
    <n-grid x-gap="12" y-gap="12" cols="1 m:2 l:3" responsive="screen" style="margin-bottom: 12px">
      <n-gi>
        <n-card class="chart-card" title="测试结果分布" hoverable>
          <div ref="testResultChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card class="chart-card" title="用例优先级分布" hoverable>
          <div ref="priorityChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card class="chart-card" title="测试用例类型分布" hoverable>
          <div ref="moduleChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 第二行：测试趋势（全宽） -->
    <n-grid x-gap="12" y-gap="12" cols="1" style="margin-bottom: 12px">
      <n-gi>
        <n-card class="chart-card wide" title="测试趋势" hoverable>
          <template #header-extra>
            <n-radio-group v-model:value="trendRange" size="small" @update:value="loadTrendChart">
              <n-radio-button value="30">近一月</n-radio-button>
              <n-radio-button value="90">近一季</n-radio-button>
              <n-radio-button value="365">近一年</n-radio-button>
            </n-radio-group>
          </template>
          <div ref="trendChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 第三行：Bug 严重程度 + Bug 状态 + Bug 错误类型 -->
    <n-grid x-gap="12" y-gap="12" cols="1 m:2 l:3" responsive="screen" style="margin-bottom: 12px">
      <n-gi>
        <n-card class="chart-card" title="Bug 严重程度分布" hoverable>
          <div ref="bugSeverityChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card class="chart-card" title="Bug 状态分布" hoverable>
          <div ref="bugStatusChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card class="chart-card" title="Bug 错误类型分布" hoverable>
          <div ref="bugTypeChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 第四行：安全测试图表 -->
    <n-grid x-gap="12" y-gap="12" cols="1 m:2 l:3" responsive="screen" style="margin-bottom: 16px">
      <n-gi>
        <n-card class="chart-card" title="安全扫描类型分布" hoverable>
          <div ref="secScanTypeChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card class="chart-card" title="安全风险等级分布" hoverable>
          <div ref="secRiskLevelChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card class="chart-card" title="漏洞严重程度汇总" hoverable>
          <div ref="secVulnSeverityChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 第五行：安全用例状态 + 邮件统计 -->
    <n-grid x-gap="12" y-gap="12" cols="1 m:2" responsive="screen" style="margin-bottom: 16px">
      <n-gi>
        <n-card class="chart-card" title="安全测试用例状态" hoverable>
          <div ref="secCaseStatusChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card class="chart-card" title="邮件发送统计" hoverable>
          <div ref="emailChartRef" class="chart-container"></div>
        </n-card>
      </n-gi>
    </n-grid>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { NCard, NTimeline, NTimelineItem, NEmpty, NRadioGroup, NRadioButton, useMessage, NGrid, NGi } from 'naive-ui'
import * as echarts from 'echarts'
import { dashboardAPI } from '@/api'

const message = useMessage()

// 时间范围
const trendRange = ref('30')

// 统计数据
const stats = ref({ testCases: 0, testReports: 0, bugReports: 0, emailsSent: 0 })
const securityStats = ref({})

// 图表引用
const testResultChartRef = ref(null)
const priorityChartRef = ref(null)
const trendChartRef = ref(null)
const moduleChartRef = ref(null)
const emailChartRef = ref(null)
const bugSeverityChartRef = ref(null)
const bugStatusChartRef = ref(null)
const bugTypeChartRef = ref(null)
const secScanTypeChartRef = ref(null)
const secRiskLevelChartRef = ref(null)
const secVulnSeverityChartRef = ref(null)
const secCaseStatusChartRef = ref(null)

// 图表实例
let testResultChart = null
let priorityChart = null
let trendChart = null
let moduleChart = null
let emailChart = null
let bugSeverityChart = null
let bugStatusChart = null
let bugTypeChart = null
let secScanTypeChart = null
let secRiskLevelChart = null
let secVulnSeverityChart = null
let secCaseStatusChart = null

// 最近活动
const recentActivities = ref([])

// 加载统计数据
const loadStats = async () => {
  try {
    const result = await dashboardAPI.getStats()
    if (result.success) stats.value = result.data
  } catch (error) {
    console.error('加载统计数据失败:', error)
  }
}

// 加载测试结果分布
const loadTestResultChart = async () => {
  try {
    const result = await dashboardAPI.getTestResultStats()
    if (result.success && testResultChartRef.value) {
      if (!testResultChart) testResultChart = echarts.init(testResultChartRef.value)
      const data = result.data || { passed: 0, failed: 0, pending: 0 }
      testResultChart.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { bottom: '5%', left: 'center' },
        series: [{
          name: '测试结果', type: 'pie', radius: ['40%', '70%'],
          avoidLabelOverlap: false,
          itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
          label: { show: false, position: 'center' },
          emphasis: { label: { show: true, fontSize: 20, fontWeight: 'bold' } },
          labelLine: { show: false },
          data: [
            { value: data.passed, name: '通过', itemStyle: { color: '#18a058' } },
            { value: data.failed, name: '失败', itemStyle: { color: '#d03050' } },
            { value: data.pending, name: '待执行', itemStyle: { color: '#f0a020' } }
          ]
        }]
      })
      setTimeout(() => testResultChart?.resize(), 100)
    }
  } catch (error) { console.error('加载测试结果分布失败:', error) }
}

// 加载优先级分布
const loadPriorityChart = async () => {
  try {
    const result = await dashboardAPI.getPriorityStats()
    if (result.success && priorityChartRef.value) {
      if (!priorityChart) priorityChart = echarts.init(priorityChartRef.value)
      const data = result.data || { '1': 0, '2': 0, '3': 0, '4': 0 }
      priorityChart.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { type: 'category', data: ['1级', '2级', '3级', '4级'] },
        yAxis: { type: 'value' },
        series: [{
          name: '用例数', type: 'bar', barWidth: '60%',
          data: [
            { value: data['1'], itemStyle: { color: '#d03050' } },
            { value: data['2'], itemStyle: { color: '#f0a020' } },
            { value: data['3'], itemStyle: { color: '#2080f0' } },
            { value: data['4'], itemStyle: { color: '#18a058' } }
          ]
        }]
      })
      setTimeout(() => priorityChart?.resize(), 100)
    }
  } catch (error) { console.error('加载优先级分布失败:', error) }
}

// 加载测试趋势
const loadTrendChart = async () => {
  try {
    const days = parseInt(trendRange.value)
    const result = await dashboardAPI.getTestTrend(days)
    if (result.success && trendChartRef.value) {
      if (!trendChart) trendChart = echarts.init(trendChartRef.value)
      const data = result.data || { dates: [], tests: [], bugs: [], cases: [] }
      let axisLabelInterval = days === 365 ? 6 : days === 90 ? 2 : 2
      trendChart.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
        legend: { data: ['测试执行', 'Bug发现', '用例新增'], top: 10 },
        grid: { left: '50px', right: '20px', bottom: '60px', top: '50px' },
        xAxis: { type: 'category', boundaryGap: false, data: data.dates, axisLabel: { rotate: 45, interval: axisLabelInterval, fontSize: 11 } },
        yAxis: { type: 'value' },
        series: [
          {
            name: '测试执行', type: 'line', smooth: true, data: data.tests,
            itemStyle: { color: '#2080f0' },
            areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(32, 128, 240, 0.3)' }, { offset: 1, color: 'rgba(32, 128, 240, 0.1)' }]) }
          },
          { name: 'Bug发现', type: 'line', smooth: true, data: data.bugs, itemStyle: { color: '#d03050' } },
          { name: '用例新增', type: 'line', smooth: true, data: data.cases, itemStyle: { color: '#18a058' } }
        ]
      })
      setTimeout(() => trendChart?.resize(), 100)
    }
  } catch (error) { console.error('加载测试趋势失败:', error) }
}

// 加载模块分布
const loadModuleChart = async () => {
  try {
    const result = await dashboardAPI.getCaseTypeStats()
    if (result.success && moduleChartRef.value) {
      if (!moduleChart) moduleChart = echarts.init(moduleChartRef.value)
      const data = result.data || []
      const colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4']
      moduleChart.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { type: 'scroll', orient: 'vertical', right: 10, top: 20, bottom: 20 },
        series: [{
          name: '用例类型分布', type: 'pie', radius: '65%', center: ['40%', '50%'],
          data: data.map((item, i) => ({ value: item.count, name: item.case_type || '未分类', itemStyle: { color: colors[i % 8] } })),
          emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0, 0, 0, 0.5)' } }
        }]
      })
      setTimeout(() => moduleChart?.resize(), 100)
    }
  } catch (error) { console.error('加载模块分布失败:', error) }
}

// 加载邮件统计
const loadEmailChart = async () => {
  try {
    const result = await dashboardAPI.getEmailStats()
    if (result.success && emailChartRef.value) {
      if (!emailChart) emailChart = echarts.init(emailChartRef.value)
      const data = result.data || { success: 0, failed: 0, partial: 0 }
      emailChart.setOption({
        tooltip: { trigger: 'item' },
        legend: { bottom: '5%' },
        series: [{
          name: '邮件统计', type: 'pie', radius: ['30%', '60%'], roseType: 'area',
          data: [
            { value: data.success, name: '发送成功', itemStyle: { color: '#18a058' } },
            { value: data.failed, name: '发送失败', itemStyle: { color: '#d03050' } },
            { value: data.partial, name: '部分成功', itemStyle: { color: '#f0a020' } }
          ]
        }]
      })
      setTimeout(() => emailChart?.resize(), 100)
    }
  } catch (error) { console.error('加载邮件统计失败:', error) }
}

// 加载 Bug 分布图表
const loadBugCharts = async () => {
  try {
    const result = await dashboardAPI.getBugDistribution()
    if (!result.success) return
    const { by_severity, by_status, by_type } = result.data

    // Bug 严重程度 - 横向柱状图
    if (bugSeverityChartRef.value) {
      if (!bugSeverityChart) bugSeverityChart = echarts.init(bugSeverityChartRef.value)
      const severityColors = { '一级': '#d03050', '二级': '#f0a020', '三级': '#2080f0', '四级': '#18a058' }
      const levels = ['一级', '二级', '三级', '四级']
      bugSeverityChart.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: '3%', right: '10%', bottom: '3%', top: '3%', containLabel: true },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: levels.slice().reverse(), axisLabel: { fontSize: 13 } },
        series: [{
          type: 'bar', barWidth: '50%',
          data: levels.slice().reverse().map(l => ({ value: by_severity[l] || 0, itemStyle: { color: severityColors[l], borderRadius: [0, 6, 6, 0] } })),
          label: { show: true, position: 'right', fontSize: 13, fontWeight: 'bold' }
        }]
      })
      setTimeout(() => bugSeverityChart?.resize(), 100)
    }

    // Bug 状态 - 环形图
    if (bugStatusChartRef.value) {
      if (!bugStatusChart) bugStatusChart = echarts.init(bugStatusChartRef.value)
      const statusColors = { '待处理': '#f0a020', '已确认': '#2080f0', '已修复': '#18a058', '已关闭': '#909399' }
      bugStatusChart.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { bottom: '5%', left: 'center' },
        series: [{
          type: 'pie', radius: ['40%', '70%'], avoidLabelOverlap: false,
          itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
          label: { show: false, position: 'center' },
          emphasis: { label: { show: true, fontSize: 18, fontWeight: 'bold' } },
          labelLine: { show: false },
          data: Object.entries(by_status).filter(([, v]) => v > 0).map(([name, value]) => ({ name, value, itemStyle: { color: statusColors[name] || '#909399' } }))
        }]
      })
      setTimeout(() => bugStatusChart?.resize(), 100)
    }

    // Bug 错误类型 - 雷达图
    if (bugTypeChartRef.value) {
      if (!bugTypeChart) bugTypeChart = echarts.init(bugTypeChartRef.value)
      const types = Object.keys(by_type)
      const values = Object.values(by_type)
      const maxVal = Math.max(...values, 1)
      bugTypeChart.setOption({
        tooltip: {},
        radar: {
          indicator: types.map(t => ({ name: t, max: maxVal + 2 })),
          shape: 'polygon', splitNumber: 4,
          axisName: { color: '#333', fontSize: 12 },
          splitArea: { areaStyle: { color: ['rgba(0,120,87,0.02)', 'rgba(0,120,87,0.05)', 'rgba(0,120,87,0.08)', 'rgba(0,120,87,0.12)'] } },
          splitLine: { lineStyle: { color: 'rgba(0,120,87,0.2)' } }
        },
        series: [{
          type: 'radar',
          data: [{ value: values, name: 'Bug 数量', areaStyle: { color: 'rgba(208,48,80,0.2)' }, lineStyle: { color: '#d03050', width: 2 }, itemStyle: { color: '#d03050' }, symbol: 'circle', symbolSize: 6 }]
        }]
      })
      setTimeout(() => bugTypeChart?.resize(), 100)
    }
  } catch (error) { console.error('加载 Bug 分布图表失败:', error) }
}

// 加载安全测试图表
const loadSecurityCharts = async () => {
  try {
    const result = await dashboardAPI.getSecurityStats()
    if (!result.success) return
    const data = result.data
    securityStats.value = data

    // 扫描类型分布 - 环形图
    if (secScanTypeChartRef.value) {
      if (!secScanTypeChart) secScanTypeChart = echarts.init(secScanTypeChartRef.value)
      const typeLabels = { 
        nuclei: 'Nuclei', 
        sqlmap: 'SQLMap', 
        xsstrike: 'XSStrike', 
        fuzz: 'Fuzz',
        full_scan: '全面扫描'
      }
      const typeDescriptions = {
        nuclei: 'Nuclei 漏洞扫描',
        sqlmap: 'SQLMap SQL注入',
        xsstrike: 'XSStrike XSS检测',
        fuzz: 'Fuzz 模糊测试',
        full_scan: '全面安全扫描'
      }
      const typeColors = { 
        nuclei: '#5470c6', 
        sqlmap: '#ee6666', 
        xsstrike: '#91cc75', 
        fuzz: '#fac858',
        full_scan: '#73c0de'
      }
      const byType = data.by_type || {}
      
      // 计算总数
      const total = Object.values(byType).reduce((sum, val) => sum + val, 0)
      
      // 过滤掉值为0的项
      const chartData = Object.entries(byType)
        .filter(([, val]) => val > 0)
        .map(([key, val]) => ({
          value: val, 
          name: typeLabels[key] || key,
          description: typeDescriptions[key] || key,
          itemStyle: { 
            color: typeColors[key] || '#73c0de'
          }
        }))
      
      secScanTypeChart.setOption({
        tooltip: { 
          trigger: 'item',
          formatter: (params) => {
            const percent = ((params.value / total) * 100).toFixed(1)
            return `<div style="padding: 8px;">
              <div style="font-weight: bold; margin-bottom: 4px;">${params.data.description}</div>
              <div style="color: #666;">扫描次数: <span style="color: ${params.color}; font-weight: bold;">${params.value}</span></div>
              <div style="color: #666;">占比: <span style="color: ${params.color}; font-weight: bold;">${percent}%</span></div>
            </div>`
          },
          backgroundColor: 'rgba(255, 255, 255, 0.95)',
          borderColor: '#ddd',
          borderWidth: 1,
          textStyle: { color: '#333' }
        },
        legend: { 
          orient: 'vertical', 
          right: '5%', 
          top: 'middle',
          itemGap: 12,
          itemWidth: 14,
          itemHeight: 14,
          textStyle: {
            fontSize: 13,
            color: '#333'
          },
          formatter: (name) => {
            const item = chartData.find(d => d.name === name)
            return item ? `${name}  ${item.value}` : name
          }
        },
        series: [{
          name: '扫描类型', 
          type: 'pie', 
          radius: ['40%', '70%'], 
          center: ['35%', '50%'],
          avoidLabelOverlap: false,
          itemStyle: { 
            borderRadius: 10,
            borderColor: '#fff',
            borderWidth: 2
          },
          label: { 
            show: false,
            position: 'center'
          },
          emphasis: { 
            label: { 
              show: true, 
              fontSize: 16, 
              fontWeight: 'bold'
            }
          },
          labelLine: { 
            show: false
          },
          data: chartData.length > 0 ? chartData : [
            { value: 1, name: '暂无数据', itemStyle: { color: '#e0e0e0' } }
          ]
        }]
      })
      setTimeout(() => secScanTypeChart?.resize(), 100)
    }

    // 风险等级分布 - 仪表盘风格柱状图
    if (secRiskLevelChartRef.value) {
      if (!secRiskLevelChart) secRiskLevelChart = echarts.init(secRiskLevelChartRef.value)
      const riskColors = { A: '#18a058', B: '#2080f0', C: '#f0a020', D: '#d03050' }
      const byRisk = data.by_risk_level || {}
      secRiskLevelChart.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: '3%', right: '4%', bottom: '3%', top: '10%', containLabel: true },
        xAxis: { type: 'category', data: ['A 级', 'B 级', 'C 级', 'D 级'] },
        yAxis: { type: 'value', minInterval: 1 },
        series: [{
          type: 'bar', barWidth: '50%',
          data: ['A', 'B', 'C', 'D'].map(l => ({
            value: byRisk[l] || 0,
            itemStyle: { color: riskColors[l], borderRadius: [6, 6, 0, 0] }
          })),
          label: { show: true, position: 'top', fontSize: 14, fontWeight: 'bold' }
        }]
      })
      setTimeout(() => secRiskLevelChart?.resize(), 100)
    }

    // 漏洞严重程度汇总 - 横向柱状图
    if (secVulnSeverityChartRef.value) {
      if (!secVulnSeverityChart) secVulnSeverityChart = echarts.init(secVulnSeverityChartRef.value)
      const vuln = data.vuln_severity || {}
      const sevLabels = ['严重', '高危', '中危', '低危', '信息']
      const sevKeys = ['critical', 'high', 'medium', 'low', 'info']
      const sevColors = ['#d03050', '#ee6666', '#f0a020', '#2080f0', '#909399']
      secVulnSeverityChart.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: '3%', right: '10%', bottom: '3%', top: '3%', containLabel: true },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: sevLabels.slice().reverse(), axisLabel: { fontSize: 13 } },
        series: [{
          type: 'bar', barWidth: '45%',
          data: sevKeys.slice().reverse().map((k, i) => ({
            value: vuln[k] || 0,
            itemStyle: { color: sevColors[sevColors.length - 1 - i], borderRadius: [0, 6, 6, 0] }
          })),
          label: { show: true, position: 'right', fontSize: 13, fontWeight: 'bold' }
        }]
      })
      setTimeout(() => secVulnSeverityChart?.resize(), 100)
    }

    // 安全用例状态 - 环形图
    if (secCaseStatusChartRef.value) {
      if (!secCaseStatusChart) secCaseStatusChart = echarts.init(secCaseStatusChartRef.value)
      const cs = data.case_status || {}
      secCaseStatusChart.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { bottom: '5%', left: 'center' },
        series: [{
          name: '用例状态', type: 'pie', radius: ['40%', '70%'],
          avoidLabelOverlap: false,
          itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
          label: { show: false, position: 'center' },
          emphasis: { label: { show: true, fontSize: 18, fontWeight: 'bold' } },
          labelLine: { show: false },
          data: [
            { value: cs.pending || 0, name: '待测试', itemStyle: { color: '#f0a020' } },
            { value: cs.pass || 0, name: '通过', itemStyle: { color: '#18a058' } },
            { value: cs.bug || 0, name: 'Bug', itemStyle: { color: '#d03050' } }
          ]
        }]
      })
      setTimeout(() => secCaseStatusChart?.resize(), 100)
    }
  } catch (error) { console.error('加载安全测试图表失败:', error) }
}

// 加载最近活动
const loadRecentActivities = async () => {
  try {
    const result = await dashboardAPI.getRecentActivities()
    if (result.success) {
      recentActivities.value = result.data.map(item => ({
        type: item.type === 'success' ? 'success' : item.type === 'error' ? 'error' : 'info',
        title: item.title,
        content: item.content,
        time: item.time
      }))
    }
  } catch (error) { console.error('加载最近活动失败:', error) }
}

// 窗口大小改变时重绘图表
const handleResize = () => {
  testResultChart?.resize()
  priorityChart?.resize()
  trendChart?.resize()
  moduleChart?.resize()
  emailChart?.resize()
  bugSeverityChart?.resize()
  bugStatusChart?.resize()
  bugTypeChart?.resize()
  secScanTypeChart?.resize()
  secRiskLevelChart?.resize()
  secVulnSeverityChart?.resize()
  secCaseStatusChart?.resize()
}

onMounted(async () => {
  await loadStats()
  await new Promise(resolve => setTimeout(resolve, 200))
  await Promise.all([
    loadTestResultChart(),
    loadPriorityChart(),
    loadTrendChart(),
    loadModuleChart(),
    loadEmailChart(),
    loadBugCharts(),
    loadSecurityCharts(),
    loadRecentActivities()
  ])
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  testResultChart?.dispose()
  priorityChart?.dispose()
  trendChart?.dispose()
  moduleChart?.dispose()
  emailChart?.dispose()
  bugSeverityChart?.dispose()
  bugStatusChart?.dispose()
  bugTypeChart?.dispose()
  secScanTypeChart?.dispose()
  secRiskLevelChart?.dispose()
  secVulnSeverityChart?.dispose()
  secCaseStatusChart?.dispose()
})
</script>

<style scoped>
.dashboard-container {
  padding: 0 8px 8px 8px;
}

.stat-card {
  display: flex;
  align-items: center;
  padding: 18px;
  border-radius: 12px;
  background: white;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  transition: transform 0.3s, box-shadow 0.3s;
  height: 100%;
}

.stat-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.12);
}

.stat-icon {
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  font-size: 22px;
  margin-right: 14px;
  flex-shrink: 0;
}

.stat-card.test-cases .stat-icon {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}
.stat-card.test-reports .stat-icon {
  background: linear-gradient(135deg, #2080f0 0%, #18a058 100%);
  color: white;
}
.stat-card.bugs .stat-icon {
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
  color: white;
}
.stat-card.emails .stat-icon {
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
  color: white;
}
.stat-card.security .stat-icon {
  background: linear-gradient(135deg, #007857 0%, #00b894 100%);
  color: white;
}

.stat-content { flex: 1; }
.stat-value { font-size: 26px; font-weight: 700; color: #333; }
.stat-label { font-size: 13px; color: #666; margin-top: 2px; }

.chart-card {
  border-radius: 12px;
  height: 100%;
}

.chart-container {
  height: 300px;
  min-height: 300px;
  width: 100%;
}
</style>
