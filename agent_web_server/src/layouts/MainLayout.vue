<template>
  <div class="flex h-screen overflow-hidden">
    <!-- 侧边栏导航 -->
    <aside class="sidebar-gradient text-white flex flex-col flex-shrink-0 shadow-xl z-50" style="width: 240px">
      <div class="p-6 flex items-center gap-3 border-b border-white/10">
        <div class="bg-white rounded-full shadow-lg overflow-hidden flex items-center justify-center w-11 h-11">
          <img :src="logo" alt="御策天检" class="w-10 h-10 object-contain" />
        </div>
        <span class="text-xl font-bold tracking-tight">御策天检</span>
      </div>

      <nav class="flex-1 overflow-y-auto pt-4">
        <MenuItem
          v-for="menu in menuList"
          :key="menu.id"
          :menu="menu"
          :isOpen="openMenus.includes(menu.id)"
          :currentPath="currentPath"
          :openSubMenus="openSubMenus"
          @toggle="toggleMenu"
          @toggle-sub="toggleSubMenu"
        />
      </nav>
    </aside>

    <!-- 主内容区 -->
    <main class="flex-1 flex flex-col h-full overflow-hidden">
      <!-- Top Bar -->
      <header class="h-16 bg-white border-b border-slate-200 flex items-center justify-between px-8 flex-shrink-0">
        <h2 class="text-lg font-bold text-slate-700">{{ currentTitle }}</h2>
        <div class="flex items-center gap-4">
          <!-- 项目选择器 -->
          <ProjectSelector @change="handleProjectChange" ref="projectSelectorRef" />
          
          <div class="flex items-center gap-2 text-xs text-slate-500 bg-slate-100 px-3 py-1.5 rounded-full">
            <span class="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
            Browser-use 引擎就绪
          </div>
          <n-badge :value="3" :max="99">
            <n-button circle quaternary>
              <template #icon>
                <i class="fas fa-bell text-[#007857]"></i>
              </template>
            </n-button>
          </n-badge>
        </div>
      </header>

      <!-- Content Area -->
      <div class="flex-1 overflow-y-auto p-8">
        <router-view v-slot="{ Component }">
          <transition name="fade-slide" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { NBadge, NButton } from 'naive-ui'
import MenuItem from '@/components/MenuItem.vue'
import ProjectSelector from '@/components/ProjectSelector.vue'
import logo from '@/assets/logo.png'
import { listActivePlatforms } from '@/api/project'

const route = useRoute()
const projectSelectorRef = ref(null)

// 平台ID到路由映射
const platformRouteMap = {
  zentao: { label: '禅道 (Zentao)', id: 'zentao' },
  pingcode: { label: 'PingCode', id: 'pingcode' },
  worktile: { label: 'Worktile', id: 'worktile' },
  ones: { label: 'ONES', id: 'ones' },
  yunxiao: { label: '云效', id: 'yunxiao' },
  tapd: { label: 'TAPD', id: 'tapd' },
  '8manage': { label: '8Manage PM', id: '8manage' },
  msproject: { label: 'Microsoft Project', id: 'msproject' },
  asana: { label: 'Asana', id: 'asana' },
  clickup: { label: 'ClickUp', id: 'clickup' },
  jira: { label: 'Jira', id: 'jira' }
}

const menuList = ref([
  {
    id: 'dashboard',
    icon: 'fa-chart-line',
    label: '数据看板',
    path: '/dashboard'
  },
  {
    id: 'test',
    icon: 'fa-vials',
    label: '测试模块',
    children: [
      { label: '功能测试', path: '/test/func' },
      { label: '性能测试', path: '/test/press' },
      { label: '安全测试', path: '/test/security' },
      { label: '接口测试', path: '/test/api' },
      { label: '一键测试', path: '/test/oneclick' },
      { label: '页面知识库', path: '/test/knowledge' }
    ]
  },
  {
    id: 'mail',
    icon: 'fa-envelope',
    label: '邮件通知模块',
    children: [
      { label: '联系人管理', path: '/mail/contacts' },
      { label: '邮件发送', path: '/mail/send' },
      { label: '邮件配置', path: '/mail/config' }
    ]
  },
  {
    id: 'model',
    icon: 'fa-brain',
    label: '模型管理模块',
    children: [
      { label: '模型信息与切换', path: '/model/manage' },
      { label: '供应商管理', path: '/model/providers' }
    ]
  },
  {
    id: 'case',
    icon: 'fa-clipboard-list',
    label: '测试资源模块',
    children: [
      { label: '项目管理', path: '/case/project' },
      { label: '用例生成', path: '/case/generate' },
      { label: '用例管理', path: '/case/manage' },
      { label: '接口文件管理', path: '/case/api-spec' },
      { label: '用例模板配置', path: '/case/template' }
    ]
  },
  {
    id: 'prompt',
    icon: 'fa-terminal',
    label: '技能模块',
    children: [
      { label: 'Skills仓库', path: '/prompt/list' },
      { label: 'Skills管理', path: '/skills/manage' }
    ]
  },
  {
    id: 'report',
    icon: 'fa-file-alt',
    label: '测试报告模块',
    children: [
      { label: 'Bug测试报告', path: '/report/bug' },
      { label: '运行测试报告', path: '/report/run' },
      { label: '综合测试报告', path: '/report/mixed' }
    ]
  },
  {
    id: 'project',
    icon: 'fa-project-diagram',
    label: '企业项目管理模块',
    children: []
  }
])

const openMenus = ref(['test'])
const openSubMenus = ref([])
const currentPath = computed(() => route.path)
const currentTitle = computed(() => route.meta?.title || '请选择功能模块')

// 动态加载项目管理平台菜单
const loadProjectPlatforms = async () => {
  const projectMenu = menuList.value.find(m => m.id === 'project')
  if (!projectMenu) return
  
  // 总控制台始终显示在第一位
  const children = [
    { label: '平台总控制台', path: '/project/control' }
  ]
  
  try {
    const res = await listActivePlatforms()
    if (res.success && res.data) {
      // 添加已激活的平台
      res.data.forEach(platform => {
        const platformInfo = platformRouteMap[platform.platform_id]
        if (platformInfo) {
          children.push({
            id: platform.platform_id,
            label: platformInfo.label,
            children: [
              { label: '用例导入', path: `/project/${platform.platform_id}/cases` },
              { label: 'Bug推送与同步', path: `/project/${platform.platform_id}/bugs` }
            ]
          })
        }
      })
    }
  } catch (error) {
    console.error('加载项目管理平台菜单失败:', error)
  } finally {
    // 无论是否成功加载平台列表，都要设置菜单（至少包含总控制台）
    projectMenu.children = children
  }
}

onMounted(() => {
  loadProjectPlatforms()
})

// 根据路由自动展开对应菜单（手风琴效果）
watch(
  () => route.path,
  (path) => {
    // 自动展开一级菜单
    const menuId = route.meta?.menu
    if (menuId) {
      openMenus.value = [menuId]
    }
    
    // 自动展开二级菜单（三级菜单的父级）
    if (path.startsWith('/project/')) {
      const parts = path.split('/')
      if (parts.length >= 3) {
        const subMenuId = parts[2] // 例如：zentao, pingcode
        openSubMenus.value = [subMenuId]
      }
    }
  },
  { immediate: true }
)

const toggleMenu = (menuId) => {
  if (openMenus.value[0] === menuId) {
    openMenus.value = []
    openSubMenus.value = []
  } else {
    openMenus.value = [menuId]
    openSubMenus.value = []
  }
}

const toggleSubMenu = (subMenuId) => {
  const index = openSubMenus.value.indexOf(subMenuId)
  if (index > -1) {
    openSubMenus.value.splice(index, 1)
  } else {
    openSubMenus.value.push(subMenuId)
  }
}

const handleProjectChange = (projectId) => {
  console.log('项目切换:', projectId)
  // 项目切换后，ProjectSelector 组件会自动刷新页面
}
</script>

<style scoped>
.sidebar-gradient {
  background: linear-gradient(180deg, #007857 0%, #004d38 100%);
}

/* 页面切换动画 - 丝滑过渡效果 */
.fade-slide-enter-active {
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.fade-slide-leave-active {
  transition: all 0.2s cubic-bezier(0.4, 0, 1, 1);
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateX(20px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateX(-20px);
}

.fade-slide-enter-to,
.fade-slide-leave-from {
  opacity: 1;
  transform: translateX(0);
}
</style>
