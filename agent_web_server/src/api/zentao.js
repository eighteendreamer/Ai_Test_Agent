import api from './index'

// 禅道集成 API
export const zentaoAPI = {
  // ========== 配置管理 ==========
  getConfigs() {
    return api.get('/zentao/config/list')
  },
  getActiveConfig() {
    return api.get('/zentao/config/active')
  },
  createConfig(data) {
    return api.post('/zentao/config', data)
  },
  updateConfig(id, data) {
    return api.put(`/zentao/config/${id}`, data)
  },
  deleteConfig(id) {
    return api.delete(`/zentao/config/${id}`)
  },
  activateConfig(id) {
    return api.post(`/zentao/config/${id}/activate`)
  },
  testConnection() {
    return api.post('/zentao/config/test-connection')
  },

  // ========== Bug 推送 / 同步 ==========
  pushBugs(bug_ids, product_id = null, severity = 3, pri = 3) {
    return api.post('/zentao/bugs/push', { bug_ids, product_id, severity, pri })
  },
  syncBugStatus(bug_id = null) {
    const params = {}
    if (bug_id) params.bug_id = bug_id
    return api.post('/zentao/bugs/sync', null, { params })
  },

  // ========== 用例导入 ==========
  importCases(data) {
    return api.post('/zentao/cases/import', data)
  },
  getProducts() {
    return api.get('/zentao/products')
  }
}
