/**
 * 测试步骤字段处理工具
 *
 * 数据库 steps 字段存储格式为 JSON 字符串，可能是：
 *   - '[{"step":"...","expected":"..."},...]'  （禅道导入 / AI 生成）
 *   - '["step1","step2"]'                      （旧字符串数组）
 *   - '"单条步骤描述"'                           （纯文本）
 *
 * 使用场景：
 *   parseSteps(raw)        → [{step, expected}]  用于详情展示
 *   stepsToEditArray(raw)  → ["step1","step2"]   用于编辑表单（string[]）
 *   editArrayToJson(arr)   → JSON string          提交时写回数据库
 */

/**
 * 将任意格式的 steps 原始值解析为标准对象数组 [{step, expected}]
 * @param {string|Array|null} raw
 * @returns {{ step: string, expected: string }[]}
 */
export function parseSteps(raw) {
  if (!raw) return []

  let arr = raw

  // 如果是字符串，先 JSON.parse
  if (typeof arr === 'string') {
    const trimmed = arr.trim()
    if (!trimmed) return []
    try {
      arr = JSON.parse(trimmed)
    } catch {
      // 普通纯文本，当成一条步骤
      return [{ step: trimmed, expected: '' }]
    }
  }

  if (!Array.isArray(arr)) {
    // 可能是单个对象
    if (arr && typeof arr === 'object') {
      return [{ step: arr.step || arr.desc || String(arr), expected: arr.expected || arr.expect || '' }]
    }
    return []
  }

  return arr.map(item => {
    if (typeof item === 'string') return { step: item, expected: '' }
    if (item && typeof item === 'object') {
      return {
        step: item.step || item.desc || '',
        expected: item.expected || item.expect || ''
      }
    }
    return { step: String(item ?? ''), expected: '' }
  }).filter(s => s.step)
}

/**
 * 将 steps 原始值转为编辑表单所需的字符串数组
 * @param {string|Array|null} raw
 * @returns {string[]}
 */
export function stepsToEditArray(raw) {
  const parsed = parseSteps(raw)
  return parsed.length > 0 ? parsed.map(s => s.step) : ['']
}

/**
 * 将编辑表单的字符串数组序列化回 JSON 字符串（提交后端时使用）
 * @param {string[]} arr
 * @returns {string}
 */
export function editArrayToJson(arr) {
  const cleaned = (arr || []).filter(s => s && s.trim())
  return JSON.stringify(cleaned.map(step => ({ step, expected: '' })))
}
