/**
 * 懒加载Composable
 * 用于列表数据的分页加载和无限滚动
 */
import { ref, reactive, computed, watch } from 'vue'

export function useLazyLoad(options = {}) {
  const {
    fetchFunction, // 数据获取函数
    pageSize: initialPageSize = 20, // 每页数据量
    autoLoad = true, // 是否自动加载
    filters = reactive({}), // 筛选条件
    debounceDelay = 300 // 防抖延迟
  } = options

  // 数据状态
  const data = ref([])
  const loading = ref(false)
  const currentPage = ref(1)
  const pageSize = ref(initialPageSize)
  const total = ref(0)
  const hasMore = ref(true)

  // 计算属性
  const totalPages = computed(() => Math.ceil(total.value / pageSize.value))
  const isEmpty = computed(() => !loading.value && data.value.length === 0)
  
  // 防抖定时器
  let debounceTimer = null

  /**
   * 加载数据
   * @param {boolean} append - 是否追加到现有数据（用于无限滚动）
   */
  const loadData = async (append = false) => {
    if (loading.value) return

    loading.value = true
    try {
      const params = {
        limit: pageSize.value,
        offset: append ? data.value.length : (currentPage.value - 1) * pageSize.value,
        ...filters
      }

      const result = await fetchFunction(params)
      
      if (result.success || Array.isArray(result)) {
        const newData = result.data || result
        
        if (append) {
          data.value = [...data.value, ...newData]
        } else {
          data.value = newData
        }
        
        total.value = result.total || data.value.length
        hasMore.value = data.value.length < total.value
      } else {
        throw new Error(result.message || '加载失败')
      }
    } catch (error) {
      console.error('[useLazyLoad] 加载数据失败:', error)
      throw error
    } finally {
      loading.value = false
    }
  }

  /**
   * 加载更多（无限滚动）
   */
  const loadMore = async () => {
    if (!hasMore.value || loading.value) return
    await loadData(true)
  }

  /**
   * 刷新数据（回到第一页）
   */
  const refresh = async () => {
    currentPage.value = 1
    await loadData(false)
  }

  /**
   * 跳转到指定页
   */
  const goToPage = async (page) => {
    if (page < 1 || page > totalPages.value) return
    currentPage.value = page
    await loadData(false)
  }

  /**
   * 修改每页大小
   */
  const changePageSize = async (size) => {
    pageSize.value = size
    currentPage.value = 1
    await loadData(false)
  }

  /**
   * 防抖搜索
   */
  const debouncedRefresh = () => {
    if (debounceTimer) {
      clearTimeout(debounceTimer)
    }
    debounceTimer = setTimeout(() => {
      refresh()
    }, debounceDelay)
  }

  // 监听筛选条件变化（防抖）
  if (filters) {
    watch(
      () => ({ ...filters }),
      () => {
        debouncedRefresh()
      },
      { deep: true }
    )
  }

  // 自动加载初始数据
  if (autoLoad) {
    loadData()
  }

  return {
    // 数据
    data,
    loading,
    currentPage,
    pageSize,
    total,
    hasMore,
    isEmpty,
    totalPages,
    
    // 方法
    loadData,
    loadMore,
    refresh,
    goToPage,
    changePageSize,
    debouncedRefresh
  }
}

/**
 * 无限滚动Composable
 * 用于在滚动到底部时自动加载更多数据
 */
export function useInfiniteScroll(containerRef, loadMore, options = {}) {
  const {
    threshold = 100, // 距离底部多少像素时触发
    disabled = ref(false)
  } = options

  const handleScroll = () => {
    if (disabled.value) return

    const container = containerRef.value
    if (!container) return

    const { scrollTop, scrollHeight, clientHeight } = container
    const distanceToBottom = scrollHeight - scrollTop - clientHeight

    if (distanceToBottom < threshold) {
      loadMore()
    }
  }

  // 节流处理
  let throttleTimer = null
  const throttledScroll = () => {
    if (throttleTimer) return
    throttleTimer = setTimeout(() => {
      handleScroll()
      throttleTimer = null
    }, 200)
  }

  return {
    handleScroll: throttledScroll
  }
}
