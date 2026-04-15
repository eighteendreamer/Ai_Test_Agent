/**
 * 用例模板配置 API
 */
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8001'

export const getActiveTemplate = () =>
  axios.get(`${API_BASE}/api/case-template/active`).then(r => r.data)

export const listTemplates = () =>
  axios.get(`${API_BASE}/api/case-template/list`).then(r => r.data)

export const syncFromPlatform = (platformId) =>
  axios.post(`${API_BASE}/api/case-template/sync/${platformId}`).then(r => r.data)

export const updateTemplate = (templateId, data) =>
  axios.put(`${API_BASE}/api/case-template/${templateId}`, data).then(r => r.data)

export const resetToDefault = () =>
  axios.post(`${API_BASE}/api/case-template/reset`).then(r => r.data)
