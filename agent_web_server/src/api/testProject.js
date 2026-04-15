/**
 * 测试项目管理 API
 */
import request from './index'

/**
 * 获取所有项目列表
 */
export const getProjects = () => {
  return request.get('/projects/list')
}

/**
 * 获取单个项目详情
 */
export const getProject = (id) => {
  return request.get(`/projects/${id}`)
}

/**
 * 创建项目
 */
export const createProject = (data) => {
  return request.post('/projects/create', data)
}

/**
 * 更新项目
 */
export const updateProject = (id, data) => {
  return request.put(`/projects/${id}`, data)
}

/**
 * 删除项目
 */
export const deleteProject = (id) => {
  return request.delete(`/projects/${id}`)
}

/**
 * 设置默认项目
 */
export const setDefaultProject = (id) => {
  return request.put(`/projects/${id}/set-default`)
}

/**
 * 启用/禁用项目
 */
export const toggleProjectStatus = (id, isActive) => {
  return request.put(`/projects/${id}/toggle`, { is_active: isActive })
}

/**
 * 从项目管理平台导入项目
 */
export const importPlatformProject = (data) => {
  return request.post('/projects/import-platform-product', data)
}
