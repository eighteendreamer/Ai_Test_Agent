<template>
  <div>
    <!-- 顶部 -->
    <div class="flex items-center justify-between mb-6">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 flex items-center justify-center shadow-lg">
          <i class="fas fa-puzzle-piece text-white text-lg"></i>
        </div>
        <div>
          <h2 class="text-xl font-bold text-slate-800">Skills 管理</h2>
          <p class="text-xs text-slate-400">管理已安装的 AI 测试技能</p>
        </div>
      </div>
      <n-button type="primary" @click="showInstall = true">
        <template #icon><i class="fas fa-plus"></i></template>
        安装 Skill
      </n-button>
    </div>

    <!-- 分类筛选 -->
    <div class="flex gap-2 mb-4">
      <n-tag v-for="cat in categories" :key="cat.value"
        :type="filterCategory === cat.value ? 'primary' : 'default'"
        :bordered="filterCategory !== cat.value"
        round class="cursor-pointer" @click="filterCategory = cat.value">
        {{ cat.label }}
      </n-tag>
    </div>

    <!-- Skills 卡片列表 -->
    <div v-if="filteredSkills.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      <div v-for="skill in filteredSkills" :key="skill.id"
        class="bg-white rounded-2xl border border-slate-100 shadow-sm hover:shadow-md transition-all p-5 flex flex-col">
        <!-- 头部 -->
        <div class="flex items-start justify-between mb-3">
          <div class="w-10 h-10 rounded-xl flex items-center justify-center text-lg"
            :class="categoryIcon(skill.category).bg">
            <i :class="categoryIcon(skill.category).icon" class="text-white"></i>
          </div>
          <n-switch v-model:value="skill.is_active" :checked-value="1" :unchecked-value="0"
            @update:value="(v) => toggleSkill(skill.id, v === 1)" size="small" />
        </div>

        <!-- 信息 -->
        <h3 class="font-bold text-slate-800 text-sm mb-1">{{ skill.name }}</h3>
        <p class="text-xs text-slate-400 mb-1">{{ skill.slug || skill.source }}</p>
        <p class="text-xs text-slate-500 flex-1 line-clamp-2 mb-3">{{ skill.description || '暂无描述' }}</p>

        <!-- 底部 -->
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <n-tag size="tiny" round :bordered="false" :type="categoryTagType(skill.category)">
              {{ skill.category || 'general' }}
            </n-tag>
            <span class="text-xs text-slate-400">{{ skill.author }}</span>
          </div>
          <div class="flex gap-1">
            <n-button size="tiny" quaternary @click="viewDetail(skill.id)">
              <template #icon><i class="fas fa-eye text-xs"></i></template>
            </n-button>
            <n-button size="tiny" quaternary type="error" @click="uninstallSkill(skill.id, skill.name)">
              <template #icon><i class="fas fa-trash text-xs"></i></template>
            </n-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="text-center py-16 text-slate-400">
      <i class="fas fa-puzzle-piece text-4xl mb-4 text-slate-300"></i>
      <p class="text-lg">暂无已安装的 Skills</p>
      <p class="text-sm mt-1">点击右上角"安装 Skill"开始</p>
    </div>

    <!-- 安装弹窗 -->
    <n-modal v-model:show="showInstall" preset="card" title="安装 Skill" style="width: 560px">
      <n-tabs type="segment" animated>
        <!-- Tab 1: 上传文件（推荐） -->
        <n-tab-pane name="upload" tab="📁 上传文件">
          <div class="space-y-4 pt-2">
            <n-upload
              :max="1"
              accept=".md"
              :default-upload="false"
              @change="handleFileChange"
            >
              <n-upload-dragger>
                <div class="py-4">
                  <i class="fas fa-cloud-upload-alt text-3xl text-emerald-400 mb-2"></i>
                  <p class="text-sm text-slate-500">点击或拖拽 .md 文件到此处</p>
                  <p class="text-xs text-slate-400 mt-1">支持 Markdown 格式的 Skill 文件</p>
                </div>
              </n-upload-dragger>
            </n-upload>
            <n-input v-model:value="uploadSkillName" placeholder="Skill 名称（可选，默认使用文件名）" size="small" />
            <n-input v-model:value="uploadDescription" placeholder="描述（可选）" size="small" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
            <div class="flex justify-end">
              <n-button type="primary" :loading="uploading" :disabled="!uploadFile" @click="doUpload">
                <template #icon><i class="fas fa-upload"></i></template>
                上传安装
              </n-button>
            </div>
          </div>
        </n-tab-pane>

        <!-- Tab 2: GitHub 安装 -->
        <n-tab-pane name="github" tab="🐙 GitHub">
          <div class="space-y-4 pt-2">
            <n-input v-model:value="installSlug" placeholder="输入 GitHub 仓库标识，如 anthropics/webapp-testing">
              <template #prefix><i class="fab fa-github text-slate-400 mr-1"></i></template>
            </n-input>
            <div class="text-xs text-slate-400">
              从 GitHub 下载 Skill 文件。格式: owner/repo。需要能访问 GitHub。
            </div>

            <!-- 搜索 -->
            <div class="border-t pt-4">
              <div class="flex gap-2 mb-3">
                <n-input v-model:value="searchQuery" placeholder="搜索 Skills..." size="small" @keydown.enter="searchSkills" />
                <n-button size="small" @click="searchSkills" :loading="searching">搜索</n-button>
              </div>
              <div v-if="searchResults.length > 0" class="space-y-2 max-h-60 overflow-y-auto">
                <div v-for="item in searchResults" :key="item.slug"
                  class="flex items-center justify-between p-2 bg-slate-50 rounded-lg hover:bg-emerald-50 transition-colors">
                  <div class="flex-1 min-w-0">
                    <div class="text-sm font-medium text-slate-700 truncate">{{ item.name }}</div>
                    <div class="text-xs text-slate-400 truncate">{{ item.slug }} · ⭐ {{ item.stars }}</div>
                  </div>
                  <n-button size="tiny" type="primary" ghost @click="installSlug = item.slug">选择</n-button>
                </div>
              </div>
            </div>
            <div class="flex justify-end">
              <n-button type="primary" :loading="installing" :disabled="!installSlug.trim()" @click="doInstall">
                安装
              </n-button>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>
    </n-modal>

    <!-- 详情弹窗 -->
    <n-modal v-model:show="showDetail" preset="card" title="Skill 详情" style="width: 640px">
      <div v-if="detailData">
        <div class="flex items-center gap-3 mb-4">
          <h3 class="text-lg font-bold">{{ detailData.name }}</h3>
          <n-tag size="small" round>{{ detailData.category }}</n-tag>
        </div>
        <p class="text-sm text-slate-500 mb-4">{{ detailData.description }}</p>
        <div class="bg-slate-50 rounded-xl p-4 max-h-96 overflow-y-auto">
          <pre class="text-xs text-slate-600 whitespace-pre-wrap">{{ detailData.content }}</pre>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { NButton, NTag, NSwitch, NModal, NInput, NTabs, NTabPane, NUpload, NUploadDragger, useMessage, useDialog } from 'naive-ui'
import { skillsAPI } from '@/api/index.js'

const message = useMessage()
const dialog = useDialog()

const skills = ref([])
const filterCategory = ref('')
const showInstall = ref(false)
const installSlug = ref('')
const installing = ref(false)
const searchQuery = ref('')
const searchResults = ref([])
const searching = ref(false)
const showDetail = ref(false)
const detailData = ref(null)

// 上传相关
const uploadFile = ref(null)
const uploadSkillName = ref('')
const uploadDescription = ref('')
const uploading = ref(false)

const categories = [
  { label: '全部', value: '' },
  { label: '测试', value: 'testing' },
  { label: '浏览器', value: 'browser' },
  { label: '接口', value: 'api' },
  { label: '通用', value: 'general' },
]

const filteredSkills = computed(() => {
  if (!filterCategory.value) return skills.value
  return skills.value.filter(s => s.category === filterCategory.value)
})

function categoryIcon(cat) {
  const map = {
    testing: { bg: 'bg-emerald-500', icon: 'fas fa-vial' },
    browser: { bg: 'bg-blue-500', icon: 'fas fa-globe' },
    api: { bg: 'bg-orange-500', icon: 'fas fa-plug' },
    general: { bg: 'bg-slate-500', icon: 'fas fa-cube' },
  }
  return map[cat] || map.general
}

function categoryTagType(cat) {
  const map = { testing: 'success', browser: 'info', api: 'warning', general: 'default' }
  return map[cat] || 'default'
}

async function loadSkills() {
  try {
    const res = await skillsAPI.getList()
    if (res.success) skills.value = res.data || []
  } catch (err) {
    console.error('加载 Skills 失败', err)
  }
}

async function doInstall() {
  if (!installSlug.value.trim()) return
  installing.value = true
  try {
    const res = await skillsAPI.install(installSlug.value.trim())
    if (res.success) {
      message.success(res.message || '安装成功')
      showInstall.value = false
      installSlug.value = ''
      loadSkills()
    } else {
      message.error(res.message || '安装失败')
    }
  } catch (err) {
    message.error('安装失败: ' + err.message)
  }
  installing.value = false
}

async function uninstallSkill(id, name) {
  dialog.warning({
    title: '确认卸载',
    content: `确定要卸载 Skill "${name}" 吗？`,
    positiveText: '卸载',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const res = await skillsAPI.uninstall(id)
        if (res.success) {
          message.success(res.message)
          loadSkills()
        } else {
          message.error(res.message)
        }
      } catch (err) {
        message.error('卸载失败')
      }
    }
  })
}

async function toggleSkill(id, active) {
  try {
    await skillsAPI.toggle(id, active)
  } catch (err) {
    message.error('操作失败')
    loadSkills()
  }
}

async function viewDetail(id) {
  try {
    const res = await skillsAPI.getDetail(id)
    if (res.success) {
      detailData.value = res.data
      showDetail.value = true
    }
  } catch (err) {
    message.error('获取详情失败')
  }
}

async function searchSkills() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  try {
    const res = await skillsAPI.search(searchQuery.value.trim())
    if (res.success) {
      searchResults.value = res.items || []
    } else {
      message.warning(res.message || '搜索失败')
    }
  } catch (err) {
    message.error('搜索失败')
  }
  searching.value = false
}

function handleFileChange({ fileList }) {
  uploadFile.value = fileList.length > 0 ? fileList[0].file : null
}

async function doUpload() {
  if (!uploadFile.value) return
  uploading.value = true
  try {
    const res = await skillsAPI.upload(uploadFile.value, uploadSkillName.value, uploadDescription.value)
    if (res.success) {
      message.success(res.message || '安装成功')
      showInstall.value = false
      uploadFile.value = null
      uploadSkillName.value = ''
      uploadDescription.value = ''
      loadSkills()
    } else {
      message.error(res.message || '安装失败')
    }
  } catch (err) {
    message.error('上传失败: ' + err.message)
  }
  uploading.value = false
}

onMounted(() => {
  loadSkills()
})
</script>
