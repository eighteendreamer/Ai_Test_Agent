<template>
  <div class="project-manage">
    <n-card title="项目管理" :bordered="false">
      <template #header-extra>
        <n-button type="primary" @click="showCreateDialog = true">
          <template #icon>
            <i class="fas fa-plus"></i>
          </template>
          新建项目
        </n-button>
      </template>

      <n-data-table
        :columns="columns"
        :data="projects"
        :loading="loading"
        :pagination="false"
        :bordered="false"
      />
    </n-card>

    <!-- 创建/编辑项目对话框 -->
    <n-modal
      v-model:show="showCreateDialog"
      preset="dialog"
      :title="editingProject ? '编辑项目' : '新建项目'"
      :positive-text="editingProject ? '保存' : '创建'"
      negative-text="取消"
      @positive-click="handleSubmit"
    >
      <n-form
        ref="formRef"
        :model="formData"
        :rules="rules"
        label-placement="left"
        label-width="100"
        class="mt-4"
      >
        <n-form-item label="项目名称" path="name">
          <n-input v-model:value="formData.name" placeholder="请输入项目名称" />
        </n-form-item>
        
        <n-form-item label="项目代码" path="code">
          <n-input
            v-model:value="formData.code"
            placeholder="请输入项目代码（英文）"
            :disabled="!!editingProject"
          />
        </n-form-item>
        
        <n-form-item label="项目描述" path="description">
          <n-input
            v-model:value="formData.description"
            type="textarea"
            placeholder="请输入项目描述"
            :rows="3"
          />
        </n-form-item>
        
        <n-form-item label="设为默认" path="is_default">
          <n-switch v-model:value="formData.is_default" />
        </n-form-item>
        
        <n-form-item label="启用状态" path="is_active">
          <n-switch v-model:value="formData.is_active" />
        </n-form-item>
      </n-form>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, h, onMounted } from 'vue'
import {
  NCard,
  NButton,
  NDataTable,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSwitch,
  NTag,
  NSpace,
  NPopconfirm,
  useMessage
} from 'naive-ui'
import {
  getProjects,
  createProject,
  updateProject,
  deleteProject,
  setDefaultProject
} from '@/api/testProject'

const message = useMessage()

const loading = ref(false)
const projects = ref([])
const showCreateDialog = ref(false)
const editingProject = ref(null)
const formRef = ref(null)

const formData = ref({
  name: '',
  code: '',
  description: '',
  is_default: false,
  is_active: true
})

const rules = {
  name: [
    { required: true, message: '请输入项目名称', trigger: 'blur' }
  ],
  code: [
    { required: true, message: '请输入项目代码', trigger: 'blur' },
    { pattern: /^[a-zA-Z0-9_-]+$/, message: '项目代码只能包含字母、数字、下划线和横线', trigger: 'blur' }
  ]
}

const columns = [
  {
    title: 'ID',
    key: 'id',
    width: 80
  },
  {
    title: '项目名称',
    key: 'name',
    render: (row) => {
      return h('div', { class: 'font-medium' }, row.name)
    }
  },
  {
    title: '项目代码',
    key: 'code',
    render: (row) => {
      return h(NTag, { type: 'info', size: 'small' }, { default: () => row.code })
    }
  },
  {
    title: '描述',
    key: 'description',
    ellipsis: {
      tooltip: true
    }
  },
  {
    title: '状态',
    key: 'is_active',
    width: 100,
    render: (row) => {
      return h(
        NTag,
        {
          type: row.is_active ? 'success' : 'default',
          size: 'small'
        },
        { default: () => row.is_active ? '启用' : '禁用' }
      )
    }
  },
  {
    title: '默认项目',
    key: 'is_default',
    width: 100,
    render: (row) => {
      if (row.is_default) {
        return h(NTag, { type: 'warning', size: 'small' }, { default: () => '默认' })
      }
      return h(
        NButton,
        {
          size: 'small',
          text: true,
          type: 'primary',
          onClick: () => handleSetDefault(row.id)
        },
        { default: () => '设为默认' }
      )
    }
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 180,
    render: (row) => {
      return row.created_at ? new Date(row.created_at).toLocaleString('zh-CN') : '-'
    }
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: (row) => {
      return h(
        NSpace,
        {},
        {
          default: () => [
            h(
              NButton,
              {
                size: 'small',
                type: 'primary',
                text: true,
                onClick: () => handleEdit(row)
              },
              { default: () => '编辑' }
            ),
            h(
              NPopconfirm,
              {
                onPositiveClick: () => handleDelete(row.id)
              },
              {
                default: () => '确定删除此项目吗？',
                trigger: () => h(
                  NButton,
                  {
                    size: 'small',
                    type: 'error',
                    text: true,
                    disabled: row.is_default
                  },
                  { default: () => '删除' }
                )
              }
            )
          ]
        }
      )
    }
  }
]

const loadProjects = async () => {
  loading.value = true
  try {
    const res = await getProjects()
    if (res.success) {
      projects.value = res.data
    }
  } catch (error) {
    message.error('加载项目列表失败')
  } finally {
    loading.value = false
  }
}

const handleEdit = (project) => {
  editingProject.value = project
  formData.value = {
    name: project.name,
    code: project.code,
    description: project.description || '',
    is_default: !!project.is_default,
    is_active: !!project.is_active
  }
  showCreateDialog.value = true
}

const handleSetDefault = async (id) => {
  try {
    const res = await setDefaultProject(id)
    if (res.success) {
      localStorage.setItem('currentProjectId', String(id))
      window.dispatchEvent(new CustomEvent('project-changed', {
        detail: {
          projectId: id,
          reason: 'set-default'
        }
      }))
      message.success('设置成功')
      loadProjects()
    } else {
      message.error(res.message || '设置失败')
    }
  } catch (error) {
    message.error('设置失败')
  }
}

const handleDelete = async (id) => {
  try {
    const res = await deleteProject(id)
    if (res.success) {
      message.success('删除成功')
      loadProjects()
    } else {
      message.error(res.message || '删除失败')
    }
  } catch (error) {
    message.error('删除失败')
  }
}

const handleSubmit = async () => {
  try {
    await formRef.value?.validate()
    
    const data = {
      name: formData.value.name,
      code: formData.value.code,
      description: formData.value.description,
      is_default: formData.value.is_default ? 1 : 0,
      is_active: formData.value.is_active ? 1 : 0
    }
    
    let res
    if (editingProject.value) {
      res = await updateProject(editingProject.value.id, data)
    } else {
      res = await createProject(data)
    }
    
    if (res.success) {
      message.success(editingProject.value ? '更新成功' : '创建成功')
      showCreateDialog.value = false
      resetForm()
      loadProjects()
    } else {
      message.error(res.message || '操作失败')
    }
  } catch (error) {
    console.error('表单验证失败:', error)
  }
}

const resetForm = () => {
  editingProject.value = null
  formData.value = {
    name: '',
    code: '',
    description: '',
    is_default: false,
    is_active: true
  }
}

onMounted(() => {
  loadProjects()
})
</script>

<style scoped>
.project-manage {
  max-width: 1400px;
}
</style>
