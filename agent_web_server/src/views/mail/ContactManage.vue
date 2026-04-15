<template>
  <div class="contact-manage-view">
    <!-- 页面说明 -->
    <n-card>
      <template #header>
        <div class="flex items-center gap-2">
          <i class="fas fa-address-book text-xl text-primary"></i>
          <span class="text-lg font-bold">联系人管理</span>
        </div>
      </template>
      <p class="text-gray-500">
        测试/开发联系人维护
      </p>
    </n-card>

    <!-- 搜索栏 -->
    <n-card style="margin-top: 20px">
      <n-form inline :model="filters" label-placement="left">
        <n-form-item label="搜索">
          <n-input 
            v-model:value="filters.search" 
            placeholder="搜索姓名或邮箱..."
            clearable
            style="width: 300px"
          >
            <template #prefix>
              <i class="fas fa-search"></i>
            </template>
          </n-input>
        </n-form-item>
      </n-form>
    </n-card>

    <!-- 联系人列表 -->
    <n-card style="margin-top: 20px">
      <template #header>
        <span class="font-bold">测试/开发联系人维护</span>
      </template>
      <template #header-extra>
        <n-button type="primary" @click="openAddModal">
          <template #icon>
            <i class="fas fa-plus"></i>
          </template>
          新增联系人
        </n-button>
      </template>

      <n-data-table
        :columns="columns"
        :data="filteredContacts"
        :loading="loading"
        :row-key="row => row.id"
        striped
      />
    </n-card>

    <!-- 添加/编辑联系人对话框 -->
    <n-modal
      v-model:show="modalVisible"
      preset="card"
      :title="isEditing ? '编辑联系人' : '新增联系人'"
      style="width: 600px"
    >
      <n-form
        ref="formRef"
        :model="formData"
        :rules="formRules"
        label-placement="left"
        label-width="120"
      >
        <n-form-item label="姓名" path="name">
          <n-input v-model:value="formData.name" placeholder="请输入姓名" />
        </n-form-item>

        <n-form-item label="邮箱" path="email">
          <n-input v-model:value="formData.email" placeholder="请输入邮箱地址" />
        </n-form-item>

        <n-form-item label="角色" path="role">
          <n-select
            v-model:value="formData.role"
            :options="roleOptions"
            placeholder="请选择角色"
          />
        </n-form-item>

        <n-form-item label="自动接收BUG">
          <n-switch v-model:value="formData.auto_receive_bug_bool" />
          <span class="tip">开启后将自动接收测试报告中的BUG</span>
        </n-form-item>
      </n-form>

      <template #footer>
        <n-space justify="end">
          <n-button @click="modalVisible = false">取消</n-button>
          <n-button type="primary" @click="submitForm" :loading="submitting">
            {{ isEditing ? '保存' : '新增' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, h, reactive, computed } from 'vue'
import {
  NCard, NButton, NDataTable, NModal, NForm, NFormItem, 
  NInput, NSelect, NSpace, NSwitch, NTag,
  useMessage, useDialog
} from 'naive-ui'
import { contactAPI } from '@/api'

const message = useMessage()
const dialog = useDialog()

// 数据
const contacts = ref([])
const loading = ref(false)
const modalVisible = ref(false)
const isEditing = ref(false)
const submitting = ref(false)
const formRef = ref(null)

// 筛选条件
const filters = reactive({
  search: ''
})

// 角色选项
const roleOptions = [
  { label: '测试负责人', value: '测试负责人' },
  { label: '开发人员', value: '开发人员' },
  { label: '产品经理', value: '产品经理' },
  { label: '项目经理', value: '项目经理' },
  { label: 'QA工程师', value: 'QA工程师' },
  { label: '其他', value: '其他' }
]

// 表单数据
const formData = reactive({
  id: null,
  name: '',
  email: '',
  role: '测试负责人',
  auto_receive_bug_bool: false
})

// 表单验证规则
const formRules = {
  name: { required: true, message: '请输入姓名', trigger: 'blur' },
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { 
      type: 'email', 
      message: '请输入有效的邮箱地址', 
      trigger: ['blur', 'input'] 
    }
  ]
}

// 过滤后的联系人
const filteredContacts = computed(() => {
  if (!filters.search) return contacts.value
  
  const searchLower = filters.search.toLowerCase()
  return contacts.value.filter(contact => 
    contact.name.toLowerCase().includes(searchLower) ||
    contact.email.toLowerCase().includes(searchLower) ||
    (contact.role && contact.role.toLowerCase().includes(searchLower))
  )
})

// 表格列定义
const columns = [
  { title: '姓名', key: 'name', width: 150 },
  { title: '邮箱', key: 'email', width: 250 },
  { 
    title: '角色', 
    key: 'role', 
    width: 150,
    render(row) {
      return h(NTag, { type: 'info', size: 'small' }, 
        { default: () => row.role || '-' }
      )
    }
  },
  { 
    title: '自动接收BUG', 
    key: 'auto_receive_bug', 
    width: 150,
    render(row) {
      return h(NSwitch, { 
        value: row.auto_receive_bug === 1,
        disabled: true
      })
    }
  },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    fixed: 'right',
    render(row) {
      return h(NSpace, {}, {
        default: () => [
          h(NButton, { 
            size: 'small', 
            type: 'primary', 
            onClick: () => editContact(row) 
          }, { 
            default: () => '编辑' 
          }),
          h(NButton, { 
            size: 'small', 
            type: 'error', 
            onClick: () => deleteContact(row) 
          }, { 
            default: () => '移除' 
          })
        ]
      })
    }
  }
]

// 加载联系人列表
const loadContacts = async () => {
  loading.value = true
  try {
    const result = await contactAPI.getList()
    if (Array.isArray(result)) {
      contacts.value = result
    } else {
      message.error('获取联系人列表失败')
    }
  } catch (error) {
    message.error('获取联系人列表失败: ' + (error.message || '未知错误'))
    console.error(error)
  } finally {
    loading.value = false
  }
}

// 搜索联系人
const searchContacts = () => {
  // 搜索已经通过 computed 属性实现了
}

// 打开添加模态框
const openAddModal = () => {
  isEditing.value = false
  resetFormData()
  modalVisible.value = true
}

// 编辑联系人
const editContact = (contact) => {
  isEditing.value = true
  formData.id = contact.id
  formData.name = contact.name
  formData.email = contact.email
  formData.role = contact.role || '测试负责人'
  formData.auto_receive_bug_bool = contact.auto_receive_bug === 1
  modalVisible.value = true
}

// 重置表单
const resetFormData = () => {
  formData.id = null
  formData.name = ''
  formData.email = ''
  formData.role = '测试负责人'
  formData.auto_receive_bug_bool = false
}

// 提交表单
const submitForm = async () => {
  try {
    await formRef.value?.validate()
  } catch (error) {
    return
  }

  submitting.value = true
  try {
    const data = {
      name: formData.name,
      email: formData.email,
      role: formData.role,
      auto_receive_bug: formData.auto_receive_bug_bool ? 1 : 0
    }

    let result
    if (isEditing.value) {
      result = await contactAPI.update(formData.id, data)
    } else {
      result = await contactAPI.add(data)
    }

    if (result.success) {
      message.success(isEditing.value ? '更新成功' : '新增成功')
      modalVisible.value = false
      loadContacts()
    } else {
      message.error(result.message || '操作失败')
    }
  } catch (error) {
    message.error('操作失败: ' + (error.message || '未知错误'))
    console.error(error)
  } finally {
    submitting.value = false
  }
}

// 删除联系人
const deleteContact = (contact) => {
  dialog.warning({
    title: '确认删除',
    content: `确定要删除联系人 "${contact.name}" 吗？`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await contactAPI.delete(contact.id)
        if (result.success) {
          message.success('删除成功')
          loadContacts()
        } else {
          message.error(result.message || '删除失败')
        }
      } catch (error) {
        message.error('删除失败: ' + (error.message || '未知错误'))
        console.error(error)
      }
    }
  })
}

// 挂载时加载数据
loadContacts()
</script>

<style scoped>
.contact-manage-view {
  padding: 0;
}

.text-primary {
  color: #007857;
}

.tip {
  margin-left: 10px;
  font-size: 12px;
  color: #909399;
}
</style>
