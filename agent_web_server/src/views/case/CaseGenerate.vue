<template>
  <div class="test-case-view">
    <!-- 模板来源提示条 -->
    <n-alert v-if="templateSource" type="success" style="margin-bottom: 16px">
      <template #icon><i class="fas fa-layer-group"></i></template>
      当前生成模板：<strong>{{ templateSource }}</strong>
      <span v-if="templateFieldCount" style="margin-left: 8px; color: #666;">（{{ templateFieldCount }} 个字段）</span>
      <n-button text size="small" style="margin-left: 12px" @click="goToTemplate">
        <template #icon><i class="fas fa-cog"></i></template>
        配置模板
      </n-button>
    </n-alert>

    <!-- 生成测试用例卡片 -->
    <n-card title="测试用例生成">
      <n-grid :cols="2" :x-gap="24">
        <!-- 左侧：输入需求 -->
        <n-gi>
          <n-form-item label="测试需求">
            <n-input
              v-model:value="form.requirement"
              type="textarea"
              :rows="10"
              placeholder="请输入测试需求，例如：用户可以登录学生端，查看课程，打开实验项目，上传并提交文件"
              style="height: 184px;"
              :input-props="{ style: 'height: 184px; resize: none;' }"
            />
          </n-form-item>
          <n-button type="primary" @click="generateTestCases" :loading="generating">
            <template #icon>
              <i class="fas fa-magic"></i>
            </template>
            生成测试用例
          </n-button>
        </n-gi>

        <!-- 右侧：上传文件 -->
        <n-gi>
          <n-form-item label="或上传文件">
            <n-upload
              ref="uploadRef"
              :default-upload="false"
              @change="handleFileChange"
              :max="1"
              accept=".md,.txt,.pdf,.doc,.docx"
              directory-dnd
            >
              <n-upload-dragger>
                <div class="upload-content">
                  <i class="fas fa-cloud-upload-alt text-4xl text-gray-400 mb-2"></i>
                  <p class="text-gray-600">拖拽文件到此处或 <em class="text-primary">点击上传</em></p>
                  <p class="text-xs text-gray-400 mt-2">支持 .md / .txt / .pdf / .doc / .docx 格式，文件大小不超过 10MB</p>
                </div>
              </n-upload-dragger>
            </n-upload>
          </n-form-item>
          <n-button
            type="success"
            @click="uploadFileAndGenerate"
            :loading="uploading"
            :disabled="!selectedFile"
          >
            <template #icon>
              <i class="fas fa-file-upload"></i>
            </template>
            上传文件并生成
          </n-button>
        </n-gi>
      </n-grid>
    </n-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard, NGrid, NGi, NFormItem, NInput, NButton, NUpload, NUploadDragger,
  NAlert, useMessage
} from 'naive-ui'
import { testCaseAPI } from '@/api'
import { getActiveTemplate } from '@/api/caseTemplate'

const message = useMessage()
const router = useRouter()

// ── 模板信息 ──────────────────────────────────────────────────────────
const templateSource = ref('')
const templateFieldCount = ref(0)

const loadTemplate = async () => {
  try {
    const res = await getActiveTemplate()
    if (!res.success) return
    const tpl = res.data
    templateSource.value = tpl.template_name || tpl.source_platform || '系统默认模板'
    templateFieldCount.value = tpl.fields?.length || 0
  } catch (e) {
    console.warn('[CaseGenerate] 加载模板失败', e)
  }
}

const goToTemplate = () => router.push('/case/template')

// ── 表单 ──────────────────────────────────────────────────────────────
const form = ref({ requirement: '' })
const generating = ref(false)
const uploading = ref(false)
const selectedFile = ref(null)
const uploadRef = ref(null)

const handleFileChange = ({ file }) => {
  selectedFile.value = file.file
}

const uploadFileAndGenerate = async () => {
  if (!selectedFile.value) {
    message.warning('请先选择文件')
    return
  }
  uploading.value = true
  try {
    const projectId = parseInt(localStorage.getItem('currentProjectId')) || null
    const result = await testCaseAPI.uploadFile(selectedFile.value, projectId)
    if (result.success) {
      message.success(result.message)
      selectedFile.value = null
      if (uploadRef.value) uploadRef.value.clear()
    } else {
      message.error(result.message)
    }
  } catch (error) {
    message.error('上传文件失败')
    console.error(error)
  } finally {
    uploading.value = false
  }
}

const generateTestCases = async () => {
  if (!form.value.requirement.trim()) {
    message.warning('请输入测试需求')
    return
  }
  generating.value = true
  try {
    const projectId = parseInt(localStorage.getItem('currentProjectId')) || null
    const result = await testCaseAPI.generate(form.value.requirement, projectId)
    if (result.success) {
      message.success(result.message)
      form.value.requirement = ''
    } else {
      message.error(result.message)
    }
  } catch (error) {
    message.error('生成测试用例失败')
    console.error(error)
  } finally {
    generating.value = false
  }
}

onMounted(loadTemplate)
</script>

<style scoped>
.test-case-view {
  padding: 0;
}

.upload-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px;
}

.text-primary {
  color: #007857;
}
</style>
