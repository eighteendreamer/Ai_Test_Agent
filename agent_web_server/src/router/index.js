import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/dashboard'
  },
  // 首页仪表盘
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/dashboard/Dashboard.vue'),
    meta: { title: '数据看板', menu: 'dashboard' }
  },
  // 测试模块
  {
    path: '/test/func',
    name: 'TestFunc',
    component: () => import('@/views/test/FuncTest.vue'),
    meta: { title: '功能测试', menu: 'test' }
  },
  {
    path: '/test/press',
    name: 'TestPress',
    component: () => import('@/views/test/PressTest.vue'),
    meta: { title: '性能测试', menu: 'test' }
  },
  {
    path: '/test/security',
    name: 'TestSecurity',
    component: () => import('@/views/test/PentestAgent.vue'),
    meta: { title: '安全测试', menu: 'test' }
  },
  {
    path: '/test/api',
    name: 'TestApi',
    component: () => import('@/views/test/ApiTest.vue'),
    meta: { title: '接口测试', menu: 'test' }
  },
  {
    path: '/test/oneclick',
    name: 'TestOneClick',
    component: () => import('@/views/test/OneClickTest.vue'),
    meta: { title: '一键测试', menu: 'test' }
  },
  {
    path: '/test/knowledge',
    name: 'TestKnowledge',
    component: () => import('@/views/test/KnowledgeBase.vue'),
    meta: { title: '页面知识库', menu: 'test' }
  },
  // 邮件通知模块
  {
    path: '/mail/contacts',
    name: 'MailContacts',
    component: () => import('@/views/mail/Contacts.vue'),
    meta: { title: '联系人管理', menu: 'mail' }
  },
  {
    path: '/mail/send',
    name: 'MailSend',
    component: () => import('@/views/mail/SendMail.vue'),
    meta: { title: '邮件发送', menu: 'mail' }
  },
  {
    path: '/mail/config',
    name: 'MailConfig',
    component: () => import('@/views/mail/EmailConfig.vue'),
    meta: { title: '邮件配置', menu: 'mail' }
  },
  // 模型管理模块
  {
    path: '/model/manage',
    name: 'ModelManage',
    component: () => import('@/views/model/ModelManage.vue'),
    meta: { title: '模型信息与切换', menu: 'model' }
  },
  {
    path: '/model/providers',
    name: 'ProviderManage',
    component: () => import('@/views/model/ProviderManage.vue'),
    meta: { title: '供应商管理', menu: 'model' }
  },
  // 测试测试资源模块
  {
    path: '/case/project',
    name: 'ProjectManage',
    component: () => import('@/views/case/ProjectManage.vue'),
    meta: { title: '项目管理', menu: 'case' }
  },
  {
    path: '/case/generate',
    name: 'CaseGenerate',
    component: () => import('@/views/case/CaseGenerate.vue'),
    meta: { title: '测试资源模块', menu: 'case' }
  },
  {
    path: '/case/manage',
    name: 'CaseManage',
    component: () => import('@/views/case/CaseManage.vue'),
    meta: { title: '用例管理', menu: 'case' }
  },
  {
    path: '/case/api-spec',
    name: 'ApiSpecManage',
    component: () => import('@/views/case/ApiSpecManage.vue'),
    meta: { title: '接口文件管理', menu: 'case' }
  },
  {
    path: '/case/template',
    name: 'CaseTemplate',
    component: () => import('@/views/case/CaseTemplate.vue'),
    meta: { title: '用例模板配置', menu: 'case' }
  },
  // 提示词模块
  {
    path: '/prompt/list',
    name: 'PromptList',
    component: () => import('@/views/prompt/PromptList.vue'),
    meta: { title: 'Skills仓库', menu: 'prompt' }
  },
  {
    path: '/skills/manage',
    name: 'SkillManage',
    component: () => import('@/views/skills/SkillManage.vue'),
    meta: { title: 'Skills管理', menu: 'prompt' }
  },
  // 测试报告模块
  {
    path: '/report/bug',
    name: 'ReportBug',
    component: () => import('@/views/report/BugReport.vue'),
    meta: { title: 'Bug测试报告', menu: 'report' }
  },
  {
    path: '/report/run',
    name: 'ReportRun',
    component: () => import('@/views/report/RunReport.vue'),
    meta: { title: '运行测试报告', menu: 'report' }
  },
  {
    path: '/report/mixed',
    name: 'ReportMixed',
    component: () => import('@/views/report/MixedReport.vue'),
    meta: { title: '综合测试报告', menu: 'report' }
  },
  // 企业项目管理模块 - 总控制台
  {
    path: '/project/control',
    name: 'ProjectControl',
    component: () => import('@/views/project/PlatformControl.vue'),
    meta: { title: '项目管理平台总控制台', menu: 'project' }
  },
  // 企业项目管理模块 - 禅道（只保留用例导入和Bug推送）
  {
    path: '/project/zentao/cases',
    name: 'ZentaoCases',
    component: () => import('@/views/zentao/ZentaoCases.vue'),
    meta: { title: '禅道用例导入', menu: 'project' }
  },
  {
    path: '/project/zentao/bugs',
    name: 'ZentaoBugs',
    component: () => import('@/views/zentao/ZentaoBugs.vue'),
    meta: { title: '禅道Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - PingCode
  {
    path: '/project/pingcode/config',
    name: 'PingCodeConfig',
    component: () => import('@/views/project/PingCodeConfig.vue'),
    meta: { title: 'PingCode配置', menu: 'project' }
  },
  {
    path: '/project/pingcode/cases',
    name: 'PingCodeCases',
    component: () => import('@/views/project/PingCodeCases.vue'),
    meta: { title: 'PingCode用例导入', menu: 'project' }
  },
  {
    path: '/project/pingcode/bugs',
    name: 'PingCodeBugs',
    component: () => import('@/views/project/PingCodeBugs.vue'),
    meta: { title: 'PingCode Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - Worktile
  {
    path: '/project/worktile/config',
    name: 'WorktileConfig',
    component: () => import('@/views/project/WorktileConfig.vue'),
    meta: { title: 'Worktile配置', menu: 'project' }
  },
  {
    path: '/project/worktile/cases',
    name: 'WorktileCases',
    component: () => import('@/views/project/WorktileCases.vue'),
    meta: { title: 'Worktile用例导入', menu: 'project' }
  },
  {
    path: '/project/worktile/bugs',
    name: 'WorktileBugs',
    component: () => import('@/views/project/WorktileBugs.vue'),
    meta: { title: 'Worktile Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - ONES
  {
    path: '/project/ones/config',
    name: 'OnesConfig',
    component: () => import('@/views/project/OnesConfig.vue'),
    meta: { title: 'ONES配置', menu: 'project' }
  },
  {
    path: '/project/ones/cases',
    name: 'OnesCases',
    component: () => import('@/views/project/OnesCases.vue'),
    meta: { title: 'ONES用例导入', menu: 'project' }
  },
  {
    path: '/project/ones/bugs',
    name: 'OnesBugs',
    component: () => import('@/views/project/OnesBugs.vue'),
    meta: { title: 'ONES Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - 云效
  {
    path: '/project/yunxiao/config',
    name: 'YunxiaoConfig',
    component: () => import('@/views/project/YunxiaoConfig.vue'),
    meta: { title: '云效配置', menu: 'project' }
  },
  {
    path: '/project/yunxiao/cases',
    name: 'YunxiaoCases',
    component: () => import('@/views/project/YunxiaoCases.vue'),
    meta: { title: '云效用例导入', menu: 'project' }
  },
  {
    path: '/project/yunxiao/bugs',
    name: 'YunxiaoBugs',
    component: () => import('@/views/project/YunxiaoBugs.vue'),
    meta: { title: '云效Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - TAPD
  {
    path: '/project/tapd/config',
    name: 'TapdConfig',
    component: () => import('@/views/project/TapdConfig.vue'),
    meta: { title: 'TAPD配置', menu: 'project' }
  },
  {
    path: '/project/tapd/cases',
    name: 'TapdCases',
    component: () => import('@/views/project/TapdCases.vue'),
    meta: { title: 'TAPD用例导入', menu: 'project' }
  },
  {
    path: '/project/tapd/bugs',
    name: 'TapdBugs',
    component: () => import('@/views/project/TapdBugs.vue'),
    meta: { title: 'TAPD Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - 8Manage PM
  {
    path: '/project/8manage/config',
    name: '8ManageConfig',
    component: () => import('@/views/project/8manageConfig.vue'),
    meta: { title: '8Manage配置', menu: 'project' }
  },
  {
    path: '/project/8manage/cases',
    name: '8ManageCases',
    component: () => import('@/views/project/8manageCases.vue'),
    meta: { title: '8Manage用例导入', menu: 'project' }
  },
  {
    path: '/project/8manage/bugs',
    name: '8ManageBugs',
    component: () => import('@/views/project/8manageBugs.vue'),
    meta: { title: '8Manage Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - Microsoft Project
  {
    path: '/project/msproject/config',
    name: 'MsProjectConfig',
    component: () => import('@/views/project/MsprojectConfig.vue'),
    meta: { title: 'Microsoft Project配置', menu: 'project' }
  },
  {
    path: '/project/msproject/cases',
    name: 'MsProjectCases',
    component: () => import('@/views/project/MsprojectCases.vue'),
    meta: { title: 'Microsoft Project用例导入', menu: 'project' }
  },
  {
    path: '/project/msproject/bugs',
    name: 'MsProjectBugs',
    component: () => import('@/views/project/MsprojectBugs.vue'),
    meta: { title: 'Microsoft Project Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - Asana
  {
    path: '/project/asana/config',
    name: 'AsanaConfig',
    component: () => import('@/views/project/AsanaConfig.vue'),
    meta: { title: 'Asana配置', menu: 'project' }
  },
  {
    path: '/project/asana/cases',
    name: 'AsanaCases',
    component: () => import('@/views/project/AsanaCases.vue'),
    meta: { title: 'Asana用例导入', menu: 'project' }
  },
  {
    path: '/project/asana/bugs',
    name: 'AsanaBugs',
    component: () => import('@/views/project/AsanaBugs.vue'),
    meta: { title: 'Asana Bug推送与同步', menu: 'project' }
  },
  // 企业项目管理模块 - ClickUp
  {
    path: '/project/clickup/config',
    name: 'ClickUpConfig',
    component: () => import('@/views/project/ClickUpConfig.vue'),
    meta: { title: 'ClickUp配置', menu: 'project' }
  },
  {
    path: '/project/clickup/cases',
    name: 'ClickUpCases',
    component: () => import('@/views/project/ClickUpCases.vue'),
    meta: { title: 'ClickUp用例导入', menu: 'project' }
  },
  {
    path: '/project/clickup/bugs',
    name: 'ClickUpBugs',
    component: () => import('@/views/project/ClickUpBugs.vue'),
    meta: { title: 'ClickUp Bug推送与同步', menu: 'project' }
  },
  // 兼容旧路由（重定向到新路由）
  {
    path: '/project/jira/cases',
    name: 'JiraCases',
    component: () => import('@/views/project/JiraCases.vue'),
    meta: { title: 'Jira鐢ㄤ緥瀵煎叆', menu: 'project' }
  },
  {
    path: '/project/jira/bugs',
    name: 'JiraBugs',
    component: () => import('@/views/project/JiraBugs.vue'),
    meta: { title: 'Jira Bug鎺ㄩ€佷笌鍚屾', menu: 'project' }
  },
  {
    path: '/zentao/cases',
    redirect: '/project/zentao/cases'
  },
  {
    path: '/zentao/bugs',
    redirect: '/project/zentao/bugs'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
