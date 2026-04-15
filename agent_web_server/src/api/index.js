import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 3000000
})

// Request interceptor
api.interceptors.request.use(
  config => {
    return config
  },
  error => {
    return Promise.reject(error)
  }
)

// Response interceptor
api.interceptors.response.use(
  response => {
    return response.data
  },
  error => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

// Test case API - 测试用例相关
export const testCaseAPI = {
  generate(requirement, project_id = null) {
    const data = { requirement }
    if (project_id) data.project_id = project_id
    return api.post('/test-cases/generate', data)
  },
  getList(params) {
    return api.get('/test-cases/list', { params })
  },
  getById(id) {
    return api.get(`/test-cases/${id}`)
  },
  create(data) {
    return api.post('/test-cases', data)
  },
  update(id, data) {
    return api.put(`/test-cases/${id}`, data)
  },
  delete(id) {
    return api.delete(`/test-cases/${id}`)
  },
  uploadFile(file, project_id = null) {
    const formData = new FormData()
    formData.append('file', file)
    if (project_id) formData.append('project_id', project_id)
    return api.post('/test-cases/upload-file', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  }
}

// Test code API - Browser-Use 执行相关
export const testCodeAPI = {
  executeBrowserUse(test_case_id, headless = true, max_steps = 20, use_vision = false) {
    return api.post('/test-code/execute-browser-use', {
      test_case_id,
      headless,
      max_steps,
      use_vision
    })
  },
  executeBatchBrowserUse(test_case_ids, headless = true, max_steps = 50, use_vision = false) {
    return api.post('/test-code/execute-batch-browser-use', {
      test_case_ids,
      headless,
      max_steps,
      use_vision
    })
  },
  pauseTask(task_id) {
    return api.post(`/test-code/pause-task/${task_id}`)
  },
  resumeTask(task_id) {
    return api.post(`/test-code/resume-task/${task_id}`)
  },
  stopTask(task_id) {
    return api.post(`/test-code/stop-task/${task_id}`)
  },
  getTaskStatus(task_id) {
    return api.get(`/test-code/task-status/${task_id}`)
  }
}

// Test report API - 测试报告相关
export const testReportAPI = {
  generate(test_result_ids, format_type = 'markdown') {
    return api.post('/reports/generate', { test_result_ids, format_type })
  },
  generateMixed(report_ids, bug_report_ids = []) {
    return api.post('/reports/generate-mixed', { report_ids, bug_report_ids })
  },
  sendReport(report_content, contact_ids) {
    return api.post('/reports/send-report', { report_content, contact_ids })
  },
  getList(params) {
    return api.get('/reports/list', { params })
  },
  getById(report_id) {
    return api.get(`/reports/${report_id}`)
  },
  download(report_id) {
    const url = `/api/reports/${report_id}/download`
    const link = document.createElement('a')
    link.href = url
    link.download = `test_report_${report_id}.html`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }
}

// Bug report API - Bug报告相关
export const bugReportAPI = {
  getList(params) {
    return api.get('/bugs/list', { params })
  },
  getById(bug_id) {
    return api.get(`/bugs/${bug_id}`)
  },
  updateStatus(bug_id, status) {
    return api.put(`/bugs/${bug_id}/status`, null, {
      params: { status }
    })
  }
}

// Model API - 模型管理相关
export const modelAPI = {
  getList() {
    return api.get('/models/')
  },
  add(data) {
    return api.post('/models/', data)
  },
  update(id, data) {
    return api.put(`/models/${id}`, data)
  },
  delete(id) {
    return api.delete(`/models/${id}`)
  },
  activate(id) {
    return api.post(`/models/${id}/activate`)
  },
  getActive() {
    return api.get('/models/active/current')
  },
  getProviders() {
    return api.get('/models/providers')
  },
  // 自动切换
  getAutoSwitchStatus() {
    return api.get('/models/auto-switch/status')
  },
  toggleAutoSwitch(enabled) {
    return api.post('/models/auto-switch/toggle', null, { params: { enabled } })
  },
  resetAutoSwitch(modelId = null) {
    const params = {}
    if (modelId) params.model_id = modelId
    return api.post('/models/auto-switch/reset', null, { params })
  },
  // Token 统计
  getTokenStatsSummary() {
    return api.get('/models/token-stats/summary')
  },
  getRecentTokenLogs(limit = 50) {
    return api.get('/models/token-stats/recent', { params: { limit } })
  },
  resetTodayTokens() {
    return api.post('/models/token-stats/reset-today')
  },
  testConnection(modelId = null) {
    const data = modelId ? { model_id: modelId } : {}
    return api.post('/models/test_connection', data)
  },
  // 供应商管理
  getAllProviders() {
    return api.get('/models/providers/all')
  },
  createProvider(data) {
    return api.post('/models/providers', data)
  },
  updateProvider(id, data) {
    return api.put(`/models/providers/${id}`, data)
  },
  deleteProvider(id) {
    return api.delete(`/models/providers/${id}`)
  },
  toggleProvider(id) {
    return api.put(`/models/providers/${id}/toggle`)
  }
}

// Contact API - 联系人相关
export const contactAPI = {
  getList(params) {
    // 如果没有参数，使用旧接口保持兼容性
    if (!params || Object.keys(params).length === 0) {
      return api.get('/contacts')
    }
    // 如果有分页参数，使用新接口
    return api.get('/contacts/list', { params })
  },
  add(data) {
    return api.post('/contacts', data)
  },
  update(id, data) {
    return api.put(`/contacts/${id}`, data)
  },
  delete(id) {
    return api.delete(`/contacts/${id}`)
  }
}

// Email API - 邮件发送记录相关
export const emailAPI = {
  // 发送自定义邮件（动态从联系人表获取收件人）
  sendEmail(contact_ids, subject, html_content, email_type = 'custom') {
    return api.post('/emails/send', {
      contact_ids,
      subject,
      html_content,
      email_type
    })
  },
  // 发送BUG通知给所有开启自动接收的联系人
  sendBugNotification(subject, html_content) {
    return api.post('/emails/send-bug-notification', null, {
      params: { subject, html_content }
    })
  },
  // 获取邮件发送记录列表
  getRecords(params) {
    return api.get('/emails/records', { params })
  },
  // 获取记录详情
  getRecordDetail(id) {
    return api.get(`/emails/records/${id}`)
  },
  // 获取统计信息
  getStatistics() {
    return api.get('/emails/statistics')
  },
  // 删除记录
  deleteRecord(id) {
    return api.delete(`/emails/records/${id}`)
  },
  
  // 邮件配置管理
  getConfigs() {
    return api.get('/emails/config')
  },
  getActiveConfig() {
    return api.get('/emails/config/active')
  },
  createConfig(data) {
    return api.post('/emails/config', data)
  },
  updateConfig(id, data) {
    return api.put(`/emails/config/${id}`, data)
  },
  activateConfig(id) {
    return api.post(`/emails/config/${id}/activate`)
  },
  deleteConfig(id) {
    return api.delete(`/emails/config/${id}`)
  }
}

// Dashboard API - 仪表盘数据
export const dashboardAPI = {
  getStats() {
    return api.get('/dashboard/stats')
  },
  getTestResultStats() {
    return api.get('/dashboard/test-result-stats')
  },
  getPriorityStats() {
    return api.get('/dashboard/priority-stats')
  },
  getTestTrend(days = 30) {
    return api.get('/dashboard/test-trend', { params: { days } })
  },
  getCaseTypeStats() {
    return api.get('/dashboard/case-type-stats')
  },
  getEmailStats() {
    return api.get('/dashboard/email-stats')
  },
  getRecentActivities() {
    return api.get('/dashboard/recent-activities')
  },
  getBugDistribution() {
    return api.get('/dashboard/bug-distribution')
  },
  getSystemLogs(limit = 50) {
    return api.get('/dashboard/system-logs', { params: { limit } })
  },
  getSecurityStats() {
    return api.get('/dashboard/security-stats')
  }
}

// Spec API - 接口文件管理
export const specAPI = {
  // 上传文件（支持多种格式）
  import(file, serviceName, baseUrl) {
    const formData = new FormData()
    formData.append('file', file)
    if (serviceName) formData.append('service_name', serviceName)
    if (baseUrl) formData.append('base_url', baseUrl)
    return api.post('/specs/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },
  // 从 URL 导入
  importFromUrl(url, serviceName, baseUrl) {
    const data = { url, service_name: serviceName }
    if (baseUrl) data.base_url = baseUrl
    return api.post('/specs/import-from-url', data)
  },
  // 从文本导入
  importFromText(content, formatHint, serviceName, baseUrl) {
    const data = {
      content,
      format_hint: formatHint,
      service_name: serviceName
    }
    if (baseUrl) data.base_url = baseUrl
    return api.post('/specs/import-from-text', data)
  },
  // 旧接口（保持兼容）
  importMd(file, serviceName, baseUrl) {
    const formData = new FormData()
    formData.append('file', file)
    if (serviceName) formData.append('service_name', serviceName)
    if (baseUrl) formData.append('base_url', baseUrl)
    return api.post('/specs/import-md', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },
  getList(params) {
    return api.get('/specs/list', { params })
  },
  getDetail(versionId) {
    return api.get(`/specs/${versionId}`)
  },
  getContent(versionId) {
    return api.get(`/specs/${versionId}/content`)
  },
  delete(versionId) {
    return api.delete(`/specs/${versionId}`)
  }
}

// API Test - 接口测试
export const apiTestAPI = {
  matchSpec(test_case_ids, top_k = 5, service_name = null) {
    const data = { test_case_ids, top_k }
    if (service_name) data.service_name = service_name
    return api.post('/api-test/match-spec', data)
  },
  matchEndpoints(test_case_ids, spec_version_id) {
    return api.post('/api-test/match-endpoints', { test_case_ids, spec_version_id })
  },
  execute(test_case_ids, spec_version_id, environment = null, mode = 'llm_enhanced', case_endpoint_map = null) {
    const data = { test_case_ids, spec_version_id, environment, mode }
    if (case_endpoint_map) data.case_endpoint_map = case_endpoint_map
    return api.post('/api-test/execute', data)
  }
}

export default api

// OneClick Test - 一键测试
export const oneclickAPI = {
  start(user_input, skill_ids = null, project_id = null) {
    const data = { user_input }
    if (skill_ids) data.skill_ids = skill_ids
    if (project_id) data.project_id = project_id
    return api.post('/oneclick/start', data)
  },
  getSession(sessionId) {
    return api.get(`/oneclick/session/${sessionId}`)
  },
  confirm(session_id, confirmed_cases = null) {
    const data = { session_id }
    if (confirmed_cases) data.confirmed_cases = confirmed_cases
    return api.post('/oneclick/confirm', data)
  },
  // 任务树确认：selections = { nodeId: true/false }，传 null 表示全选
  confirmTree(session_id, selections = null) {
    return api.post('/oneclick/confirm-tree', { session_id, selections })
  },
  stop(session_id, timeout = 10000) {
    return api.post('/oneclick/stop', { session_id }, { timeout })
  },
  getHistory(params) {
    return api.get('/oneclick/history', { params })
  }
}

// 测试环境管理
export const testEnvAPI = {
  getList(params = {}) {
    return api.get('/test-env/list', { params })
  },
  create(data) {
    return api.post('/test-env/create', data)
  },
  update(envId, data) {
    return api.put(`/test-env/${envId}`, data)
  },
  delete(envId) {
    return api.delete(`/test-env/${envId}`)
  },
  setDefault(envId) {
    return api.put(`/test-env/${envId}/set-default`)
  }
}

// Security Test - 安全测试平台 (按新方案重构)
export const securityAPI = {
  // 资产管理 API
  getTargets(params = {}) {
    return api.get('/security/targets', { params })
  },
  createTarget(data) {
    return api.post('/security/targets', data)
  },
  updateTarget(id, data) {
    return api.put(`/security/targets/${id}`, data)
  },
  deleteTarget(id) {
    return api.delete(`/security/targets/${id}`)
  },
  
  // 扫描任务 API
  getTasks(params = {}) {
    return api.get('/security/tasks', { params })
  },
  createScan(data) {
    return api.post('/security/scan', data)
  },
  getTask(taskId) {
    return api.get(`/security/tasks/${taskId}`)
  },
  stopTask(taskId) {
    return api.post(`/security/tasks/${taskId}/stop`)
  },
  deleteTask(taskId) {
    return api.delete(`/security/tasks/${taskId}`)
  },
  
  // 漏洞管理 API
  getVulnerabilities(params = {}) {
    return api.get('/security/vulnerabilities', { params })
  },
  getVulnerability(id) {
    return api.get(`/security/vulnerabilities/${id}`)
  },
  updateVulnerability(id, data) {
    return api.put(`/security/vulnerabilities/${id}`, data)
  },
  
  // 扫描日志 API
  getLogs(params = {}) {
    return api.get('/security/logs', { params })
  },
  getTaskLogs(taskId) {
    return api.get(`/security/tasks/${taskId}/logs`)
  },
  
  // 报告管理 API
  getReports(params = {}) {
    return api.get('/security/reports', { params })
  },
  generateReport(data) {
    return api.post('/security/reports', data)
  },
  downloadReport(reportId) {
    return api.get(`/security/reports/${reportId}/download`, { responseType: 'blob' })
  },
  
  // 工具状态检查
  getToolsStatus() {
    return api.get('/security/tools/status')
  },
  
  // 统计信息
  getStats() {
    return api.get('/security/stats')
  },
  
  // 兼容旧接口 (保持向后兼容)
  run(type, target, config = {}) {
    return api.post('/security/run', { type, target, config })
  },
  stop(task_id) {
    return api.post('/security/stop', { task_id })
  },
  getStatus(task_id) {
    return api.get(`/security/status/${task_id}`)
  },
  getResult(task_id) {
    return api.get(`/security/result/${task_id}`)
  },
  getHistory(params) {
    return api.get('/security/history', { params })
  },
  delete(task_id) {
    return api.delete(`/security/${task_id}`)
  },
  getCases(params) {
    return api.get('/security/cases', { params })
  },
  updateCaseStatus(caseId, status) {
    return api.put(`/security/cases/${caseId}/status`, { status })
  }
}

// Page Knowledge - 页面知识库
export const knowledgeAPI = {
  getStats() {
    return api.get('/knowledge/stats')
  },
  health() {
    return api.get('/knowledge/health')
  },
  lookup(url, query_text = '') {
    return api.post('/knowledge/lookup', { url, query_text })
  },
  getList(params = {}) {
    return api.get('/knowledge/list', { params })
  },
  getDetail(url) {
    return api.get('/knowledge/detail', { params: { url } })
  },
  delete(url) {
    return api.post('/knowledge/delete', { url })
  },
  retrieveContext(query, domain = '', limit = 5) {
    return api.post('/knowledge/retrieve-context', { query, domain, limit })
  },
  getStale(days = 30) {
    return api.get('/knowledge/stale', { params: { max_age_days: days, days } })
  },
  getCollectionConfig() {
    return api.get('/knowledge/collection-config')
  },
  saveCollectionConfig(data) {
    return api.post('/knowledge/collection-config', data)
  },
  createCollection(force = false) {
    return api.post('/knowledge/collection-create', { force })
  },
  // 页面探索专用接口
  explorePage(urlOrPayload, username = '', password = '', user_goal = '') {
    if (typeof urlOrPayload === 'object' && urlOrPayload !== null) {
      const payload = { ...urlOrPayload }
      if (!payload.goal && payload.user_goal) {
        payload.goal = payload.user_goal
      }
      return api.post('/knowledge/explore-page', payload)
    }
    return api.post('/knowledge/explore-page', {
      url: urlOrPayload,
      username,
      password,
      goal: user_goal
    })
  },
  // 停止页面探索
  stopExplore(task_id) {
    return api.post('/knowledge/stop-explore', { task_id })
  },
  // 查询探索任务状态
  getExploreStatus(task_id) {
    return api.get('/knowledge/explore-status', { params: { task_id } })
  },
  // 获取测试环境列表（用于页面探索）
  getTestEnvList(params = {}) {
    return api.get('/test-env/list', { params })
  }
}

// Skills - 技能管理
export const skillsAPI = {
  getList(category = null) {
    const params = {}
    if (category) params.category = category
    return api.get('/skills/list', { params })
  },
  install(slug) {
    return api.post('/skills/install', { slug })
  },
  upload(file, skillName = '', description = '') {
    const formData = new FormData()
    formData.append('file', file)
    if (skillName) formData.append('skill_name', skillName)
    if (description) formData.append('description', description)
    return api.post('/skills/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },
  uninstall(skillId) {
    return api.delete(`/skills/${skillId}`)
  },
  getDetail(skillId) {
    return api.get(`/skills/${skillId}/detail`)
  },
  toggle(skillId, is_active) {
    return api.put(`/skills/${skillId}/toggle`, { is_active })
  },
  search(q) {
    return api.get('/skills/search', { params: { q } })
  }
}
