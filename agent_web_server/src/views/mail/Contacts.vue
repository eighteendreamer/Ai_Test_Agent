<template>
  <div>
    <div class="glass-card overflow-hidden">
      <div class="p-6 border-b border-slate-100 flex justify-between items-center">
        <h3 class="font-bold">测试/开发联系人维护</h3>
        <div class="flex gap-2">
          <n-input v-model:value="searchText" placeholder="搜索姓名/邮箱..." style="width: 200px;">
            <template #prefix>
              <i class="fas fa-search text-slate-400"></i>
            </template>
          </n-input>
          <n-button type="primary" @click="handleAddContact">
            <template #icon>
              <i class="fas fa-plus"></i>
            </template>
            新增联系人
          </n-button>
        </div>
      </div>
      
      <n-data-table
        :columns="columns"
        :data="filteredContacts"
        :pagination="pagination"
        :bordered="false"
        :loading="loading"
      />
    </div>

    <!-- 新增/编辑联系人模态框 -->
    <n-modal v-model:show="showAddModal" preset="card" :title="editingContact ? '编辑联系人' : '新增联系人'" style="width: 500px;">
      <n-form :model="formData" label-placement="left" label-width="80">
        <n-form-item label="姓名" required>
          <n-input v-model:value="formData.name" placeholder="请输入姓名" />
        </n-form-item>
        <n-form-item label="邮箱" required>
          <n-input v-model:value="formData.email" placeholder="请输入邮箱" />
        </n-form-item>
        <n-form-item label="角色">
          <n-select v-model:value="formData.role" :options="roleOptions" />
        </n-form-item>
        <n-form-item label="自动接收">
          <n-switch v-model:value="formData.autoReceive" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="showAddModal = false">取消</n-button>
          <n-button type="primary" @click="saveContact">保存</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, computed, h, onMounted } from 'vue'
import { 
  NInput, NButton, NDataTable, NModal, NForm, NFormItem, 
  NSelect, NSwitch, NTag, NSpace, useMessage, useDialog 
} from 'naive-ui'
import { contactAPI } from '@/api'

const message = useMessage()
const dialog = useDialog()

const searchText = ref('')
const showAddModal = ref(false)
const editingContact = ref(null)
const loading = ref(false)

const formData = ref({
  name: '',
  email: '',
  role: '开发人员',
  autoReceive: true
})

const roleOptions = [
  { label: '后端开发', value: '后端开发' },
  { label: '前端开发', value: '前端开发' },
  { label: '测试负责人', value: '测试负责人' },
  { label: '项目经理', value: '项目经理' },
  { label: '开发人员', value: '开发人员' }
]

const roleMap = {
  '后端开发': { label: '后端开发', type: 'info' },
  '前端开发': { label: '前端开发', type: 'success' },
  '测试负责人': { label: '测试负责人', type: 'warning' },
  '项目经理': { label: '项目经理', type: 'error' },
  '开发人员': { label: '开发人员', type: 'default' }
}

const contacts = ref([])

const filteredContacts = computed(() => {
  if (!searchText.value) return contacts.value
  const keyword = searchText.value.toLowerCase()
  return contacts.value.filter(c => 
    c.name.toLowerCase().includes(keyword) || 
    c.email.toLowerCase().includes(keyword)
  )
})

const columns = [
  { title: '姓名', key: 'name', width: 120 },
  { title: '邮箱', key: 'email' },
  { 
    title: '角色', 
    key: 'role',
    render(row) {
      const role = roleMap[row.role] || { label: row.role, type: 'default' }
      return h(NTag, { type: role.type, size: 'small' }, { default: () => role.label })
    }
  },
  {
    title: '自动接收BUG',
    key: 'auto_receive_bug',
    render(row) {
      return h(NSwitch, { 
        value: row.auto_receive_bug === 1,
        onUpdateValue: async (val) => { 
          try {
            await contactAPI.update(row.id, { 
              name: row.name, 
              email: row.email, 
              role: row.role, 
              auto_receive_bug: val ? 1 : 0 
            })
            row.auto_receive_bug = val ? 1 : 0
            message.success('更新成功')
          } catch (error) {
            message.error('更新失败')
          }
        }
      })
    }
  },
  {
    title: '操作',
    key: 'actions',
    render(row) {
      return h(NSpace, null, {
        default: () => [
          h(NButton, { text: true, type: 'primary', onClick: () => editContact(row) }, { default: () => '编辑' }),
          h(NButton, { text: true, type: 'error', onClick: () => deleteContact(row) }, { default: () => '移除' })
        ]
      })
    }
  }
]

const pageSize = 10
const pagination = computed(() => {
  const total = filteredContacts.value.length
  if (!total || total <= pageSize) {
    return false
  }
  return {
    pageSize
  }
})

// 加载联系人列表
const loadContacts = async () => {
  loading.value = true
  try {
    const response = await contactAPI.getList()
    contacts.value = response || []
    console.log('联系人数据:', contacts.value)
  } catch (error) {
    message.error('加载联系人列表失败')
    console.error(error)
  } finally {
    loading.value = false
  }
}

const handleAddContact = () => {
  editingContact.value = null
  formData.value = { name: '', email: '', role: '开发人员', autoReceive: true }
  showAddModal.value = true
}

const editContact = (contact) => {
  editingContact.value = contact
  formData.value = { 
    name: contact.name,
    email: contact.email,
    role: contact.role,
    autoReceive: contact.auto_receive_bug === 1
  }
  showAddModal.value = true
}

const deleteContact = (contact) => {
  dialog.warning({
    title: '确认删除',
    content: `确定要移除联系人 "${contact.name}" 吗？`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await contactAPI.delete(contact.id)
        await loadContacts()
        message.success('删除成功')
      } catch (error) {
        message.error('删除失败')
      }
    }
  })
}

const saveContact = async () => {
  if (!formData.value.name || !formData.value.email) {
    message.error('请填写必填项')
    return
  }
  
  try {
    const data = {
      name: formData.value.name,
      email: formData.value.email,
      role: formData.value.role,
      auto_receive_bug: formData.value.autoReceive ? 1 : 0
    }
    
    if (editingContact.value) {
      await contactAPI.update(editingContact.value.id, data)
      message.success('更新成功')
    } else {
      await contactAPI.add(data)
      message.success('添加成功')
    }
    
    showAddModal.value = false
    editingContact.value = null
    formData.value = { name: '', email: '', role: '开发人员', autoReceive: true }
    await loadContacts()
  } catch (error) {
    message.error(editingContact.value ? '更新失败' : '添加失败')
    console.error(error)
  }
}

// 组件挂载时加载数据
onMounted(() => {
  loadContacts()
})
</script>

<style scoped>
.glass-card {
  background: white;
  border: 1px solid rgba(0, 120, 87, 0.1);
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
}
</style>
