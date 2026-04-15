/**
 * 项目管理平台 API
 */
import axios from 'axios'

// 使用与主 API 一致的实例，走 Vite proxy 转发，避免硬编码地址
const api = axios.create({
  baseURL: '/api',
  timeout: 3000000
})

api.interceptors.response.use(
  response => response.data,
  error => Promise.reject(error)
)

/**
 * 获取系统支持的所有项目管理平台列表
 */
export const getSupportedPlatforms = async () => {
  return api.get('/project-platform/platforms/supported')
}

/**
 * 获取所有项目管理平台配置列表
 */
export const listPlatforms = async () => {
  return api.get('/project-platform/list')
}

/**
 * 获取已激活的项目管理平台列表（用于动态菜单）
 */
export const listActivePlatforms = async () => {
  return api.get('/project-platform/active')
}

/**
 * 获取指定平台配置
 */
export const getPlatform = async (platformId) => {
  return api.get(`/project-platform/${platformId}`)
}

/**
 * 创建新的平台配置
 */
export const createPlatform = async (data) => {
  return api.post('/project-platform', data)
}

/**
 * 更新平台配置
 */
export const updatePlatform = async (configId, data) => {
  return api.put(`/project-platform/${configId}`, data)
}

/**
 * 删除平台配置
 */
export const deletePlatform = async (configId) => {
  return api.delete(`/project-platform/${configId}`)
}

/**
 * 激活指定平台配置
 */
export const activatePlatform = async (configId) => {
  return api.post(`/project-platform/${configId}/activate`)
}

/**
 * 取消激活指定平台配置
 */
export const deactivatePlatform = async (configId) => {
  return api.post(`/project-platform/${configId}/deactivate`)
}

/**
 * 启用指定平台配置
 */
export const enablePlatform = async (configId) => {
  return api.post(`/project-platform/${configId}/enable`)
}

/**
 * 禁用指定平台配置
 */
export const disablePlatform = async (configId) => {
  return api.post(`/project-platform/${configId}/disable`)
}

/**
 * 测试平台连接是否可达
 */
export const testConnection = async (data) => {
  return api.post('/project-platform/test-connection', data)
}

/**
 * 获取指定平台的远端项目列表
 */
export const getRemoteProjects = async (platformId) => {
  return api.get(`/project-platform/${platformId}/remote-projects`)
}

