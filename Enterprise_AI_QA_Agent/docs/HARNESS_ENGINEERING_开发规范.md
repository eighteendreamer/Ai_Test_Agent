# Harness Engineering 开发规范

版本：v1.0  
适用范围：`Enterprise_AI_QA_Agent` 全项目  
生效方式：自本文件提交后，后续前端、后端、Agent、工具、知识库、评估、测试、运维相关开发，均必须遵循本文档  
定位：本文档是项目级工程规范，不是概念说明文档。若与临时实现冲突，以本文档为准。

---

## 1. 文档目的

本项目不是普通 Web 应用，也不是单纯的 LLM Demo，而是一个面向企业自动化测试场景的 Agent 平台。  
因此，项目开发不能只关注“模型能不能回答”，必须同时关注：

- Agent 是否拿到了正确上下文
- Agent 是否被正确约束
- Agent 是否可以被验证
- Agent 是否可恢复、可追踪、可审计
- Agent 失败后系统是否能够自动纠偏
- 新增模块是否会提升系统稳定性，而不是增加系统混乱度

这套能力的总和，在本文档中统一称为 `Harness Engineering`。

本项目后续所有开发都必须遵循一个基本原则：

> 任何一个 Agent 能力模块，只有在被放入正确的 Harness 中之后，才算“完成开发”。

---

## 2. 核心定义

### 2.1 什么是 Harness

在本项目中，`Harness` 指围绕 Agent 运行的工程护栏层，包含但不限于：

- 上下文组织
- 任务分解
- 工具权限与接口规范
- 执行状态管理
- 验证与评估
- 日志与可观测性
- 恢复与回滚
- 知识更新与失效清理

### 2.2 什么是 Harness Engineering

`Harness Engineering` 不是提示词优化，也不是单纯流程编排。  
它是一套面向 Agent 系统的工程方法，目标是把“会做事的模型”变成“可上线、可维护、可演进的系统”。

### 2.3 本项目中的一句话定义

> Agent 负责执行能力，Harness 负责让执行变得稳定、可信、可控、可扩展。

---

## 3. 项目级总原则

以下原则为强约束，必须执行。

### 3.1 先设计 Harness，再接入 Agent

任何新增能力都必须先回答以下问题：

1. 它需要哪些上下文
2. 它可以调用哪些工具
3. 它的输入输出结构是什么
4. 它成功与失败如何判定
5. 它的结果由谁评估
6. 它失败后如何恢复或回退

若以上问题没有答案，不允许直接接入 Agent 模块。

### 3.2 执行者与评审者必须分离

执行任务的 Agent 不能作为最终质量判断者。  
必须通过独立的评估逻辑完成以下至少一项：

- 规则评估
- 断言评估
- Diff 评估
- 第二个评估 Agent
- 人工审批节点

### 3.3 所有关键行为必须结构化

禁止把关键状态仅保留在自然语言文本中。  
下列信息必须结构化保存：

- 会话状态
- 执行阶段
- 当前 Agent
- 当前工具
- 工具输入输出摘要
- 页面快照引用
- 验证结果
- 错误分类
- 最终结论

### 3.4 每个长任务都必须支持中断恢复

对以下任务类型，必须具备恢复设计：

- 页面探索
- 批量用例执行
- 多轮报告生成
- 知识库同步
- 长链路测试计划生成

### 3.5 失败时优先修 Harness，不优先修一次性结果

若某类问题重复出现，不允许只修某一次执行结果。  
必须定位为以下一种 Harness 缺陷并修复：

- 上下文缺失
- 任务拆分不合理
- 工具能力边界不清
- 评估标准缺失
- 状态记录不完整
- 知识失效未清理

---

## 4. 本项目必须具备的 8 层 Harness

## 4.1 Context Harness

负责给 Agent 提供正确、足够、分层的上下文。

本项目至少包含以下上下文来源：

- 页面知识库
- DOM 结构/页面探索结果
- 接口文档
- 测试规约
- 历史缺陷
- 历史执行记录
- 环境信息
- 用户任务描述

强制要求：

- 上下文必须区分静态上下文与动态上下文
- 上下文必须支持按需加载，不能默认全部塞进 prompt
- 页面知识若过期，必须带 `stale` 标记
- 上下文源必须可追踪，知道信息来自哪里

禁止：

- 把所有知识拼成一个巨型 prompt
- 使用未经版本化的临时文本作为系统事实

## 4.2 Task Harness

负责将用户目标拆成标准阶段，而不是让 Agent 自由发挥。

本项目标准任务阶段建议为：

1. 目标理解
2. 测试计划生成
3. 页面/接口探索
4. 测试用例设计
5. 脚本执行
6. 结果验证
7. 报告输出
8. 知识回写

强制要求：

- 每个任务必须有明确 `stage`
- 阶段切换必须有结构化状态变更
- 阶段失败必须可定位到具体节点

## 4.3 Tool Harness

负责约束 Agent 能做什么，以及工具如何被安全地调用。

每个工具必须具备：

- 唯一 ID
- 描述
- 输入 schema
- 输出 schema
- 超时策略
- 重试策略
- 审计日志
- 权限级别

本项目工具至少分为：

- 浏览器工具
- DOM/页面分析工具
- RAG/知识库工具
- 接口调用工具
- 文件与报告工具
- 外部系统集成工具

禁止：

- 无 schema 的工具调用
- 返回自然语言且不可解析的关键工具输出
- 默认开放所有工具给所有 Agent

## 4.4 Execution Harness

负责 LangGraph 或执行引擎层面的可控运行。

强制要求：

- 每个图节点必须有明确输入输出
- 每个节点必须有错误处理
- 每个长链路必须有 checkpoint
- 每轮执行必须记录 trace
- 执行图必须区分“计划节点”“执行节点”“评估节点”

建议：

- 使用子图管理大任务
- 使用事件流把节点状态推送给前端

## 4.5 Verification Harness

负责判断“任务是否真的完成”。

本项目中，以下验证方式至少选其二：

- UI 断言验证
- DOM 差异验证
- 接口响应验证
- 页面截图验证
- 业务规则验证
- 日志异常验证

强制要求：

- 没有验证的执行结果不能算完成
- 验证失败必须保留证据引用
- 验证结果必须可追踪到用例和执行轮次

## 4.6 Evaluation Harness

负责质量评估、覆盖率判断、结果复核。

本项目中必须遵守：

- 执行 Agent 不能直接作为最终评级来源
- 报告中的关键结论必须由独立评估层输出
- 安全测试和功能测试评估标准必须分开

建议设计：

- `Executor Agent`
- `Verifier`
- `Evaluator Agent`
- `Human Approval`

## 4.7 Observability Harness

负责让开发者和测试人员看见 Agent 在做什么。

强制要求：

- 每次执行都必须有 trace ID
- 每次工具调用都必须记录
- 每个节点开始/结束都必须留痕
- 前端必须能看到当前阶段、当前 Agent、当前状态

前端最少展示：

- 当前会话
- 当前阶段
- 当前执行节点
- 当前活动 Agent
- 工具调用摘要
- 错误原因
- 最终结论

## 4.8 Cleanup / Entropy Harness

负责长期运行后的系统收敛能力。

必须覆盖：

- 失效页面知识清理
- 重复用例去重
- 失效 selector 标记
- 历史报告归档
- 长期失败任务聚类

目标：

> 系统越用越稳，而不是越用越乱。

---

## 5. 与当前项目目录的映射规范

当前项目目录：

```text
Enterprise_AI_QA_Agent/
  Agent_Server/
    src/
  agent_web/
```

后续开发必须按以下职责分层推进。

### 5.1 后端目录职责

`Agent_Server/src/core`

- 配置
- 全局约束
- 环境变量

`Agent_Server/src/schemas`

- 所有结构化输入输出
- 会话状态
- 工具协议
- 验证结果协议

`Agent_Server/src/registry`

- Agent 注册
- Tool 注册
- 能力声明

`Agent_Server/src/graph`

- 任务图
- 子图
- 节点编排
- 执行顺序与失败策略

`Agent_Server/src/application`

- 会话用例服务
- 任务服务
- 验证与评估服务

`Agent_Server/src/runtime`

- 运行态存储
- 事件流
- trace
- checkpoint

`Agent_Server/src/api`

- 前端访问接口
- 管理接口
- 流式状态接口

### 5.2 前端目录职责

`agent_web/src/views`

- 页面级视图
- 必须按业务工作台划分，而不是只按通用组件划分

`agent_web/src/stores`

- 会话状态
- 执行状态
- 页面知识状态
- 报告状态

`agent_web/src/services`

- API 访问
- SSE / WebSocket
- 后续 trace 查询

强制要求：

- 前端页面不能只展示最终文本结果
- 必须逐步具备展示执行轨迹和验证状态的能力

---

## 6. 新增 Agent 模块的接入规范

任何新增 Agent 模块，必须满足以下流程：

1. 在 `registry` 中注册能力声明
2. 明确可访问工具范围
3. 定义输入输出 schema
4. 明确所属任务阶段
5. 接入验证逻辑
6. 接入评估逻辑
7. 接入前端状态展示

若缺任一项，则该 Agent 模块只能视为实验模块，不得进入主流程。

---

## 7. 新增 Tool 的接入规范

新增工具时，必须提供以下内容：

- 工具唯一标识
- 作用说明
- 输入 schema
- 输出 schema
- 错误码
- 超时配置
- 重试配置
- 权限配置
- 审计字段

推荐工具输出结构：

```json
{
  "ok": true,
  "summary": "操作摘要",
  "artifacts": [],
  "metrics": {},
  "error": null
}
```

禁止：

- 工具只返回一段无法解析的自然语言
- 工具失败不带错误类型
- 工具不记录输入来源

---

## 8. 状态模型规范

后端状态模型必须至少覆盖以下字段：

- `session_id`
- `task_id`
- `stage`
- `selected_agent`
- `selected_tools`
- `context_refs`
- `artifacts`
- `verification_result`
- `evaluation_result`
- `errors`
- `trace_id`

前端状态模型必须至少覆盖：

- 当前视图状态
- 当前会话状态
- 当前执行阶段
- 活跃 Agent
- 最近工具事件
- 最近验证结论
- 最近错误信息

---

## 9. 验证与评估的强制规则

### 9.1 功能执行完成不等于任务完成

以下情况均不能判定为成功：

- 页面操作完成但没有断言
- 报告生成完成但没有验证来源
- 测试用例生成完成但没有评估覆盖率

### 9.2 报告输出必须带证据引用

每个关键结论都应尽量关联以下一种或多种证据：

- 页面截图
- DOM 快照
- 接口响应
- 工具执行日志
- 历史差异记录

### 9.3 评估层不得只依赖自然语言自述

禁止只根据 “Agent 说自己已经完成” 来给出完成判断。

---

## 10. 知识库与 RAG 规范

页面知识库不是附属功能，而是 Context Harness 的核心。

必须满足：

- 每条知识有来源
- 每条知识有更新时间
- 每条知识可标记 stale
- 每条知识可被重新发现
- 每条知识能关联执行历史

建议知识对象结构：

- 页面 URL
- 页面标识
- 元素列表
- DOM 摘要
- 关联用例
- 关联截图
- 向量索引状态
- 新鲜度状态

---

## 11. 前端工作台规范

前端不是单纯聊天 UI，而是 Agent 工作台。

后续前端开发必须体现以下理念：

- 首页负责发起任务
- 任务池负责执行调度
- 知识库负责上下文管理
- 工具页负责能力管理
- 报告页负责结论与证据
- 设置页负责系统约束

禁止把所有能力都塞回一个对话框。

---

## 12. 开发流程规范

每次开发任务都按以下步骤执行：

1. 明确新增能力属于哪一层 Harness
2. 明确影响哪些目录和模块
3. 明确输入输出结构
4. 明确前端可见状态变化
5. 明确验证方式
6. 明确失败回退方式
7. 完成后补充文档或注释

如果一个需求无法归类到某层 Harness，说明设计仍不完整。

---

## 13. Code Review 检查清单

评审时必须检查：

- 是否只做了 Agent，没做 Harness
- 是否缺少 schema
- 是否缺少验证逻辑
- 是否缺少评估逻辑
- 是否缺少状态记录
- 是否缺少前端可观测性
- 是否会引入新的知识失效或状态混乱

若存在以上任一问题，默认不予通过。

---

## 14. 本项目当前阶段的实施优先级

当前建议优先建设以下能力：

### P0

- 统一 `AgentTaskState`
- 统一工具输入输出协议
- 统一事件流与 trace 模型
- 统一验证结果结构

### P1

- 页面知识库对象模型
- 执行节点与验证节点分离
- 报告页证据链展示
- 失败任务恢复机制

### P2

- 多 Agent 协同评估
- stale 知识自动清理
- 重复用例识别
- 历史失败聚类

---

## 15. 后续开发的强制执行条款

从本文件生效后：

- 新需求评估时，必须说明其属于哪一层 Harness
- 新模块开发时，必须说明其验证与评估方案
- 新页面开发时，必须说明其在工作台中的职责
- 新工具开发时，必须给出 schema 和权限边界
- 新 Agent 开发时，必须说明它的上下文来源与失败回退方案

若未说明，则视为设计不完整。

---

## 16. 附录：参考文章

以下文章是本文档的主要理论来源，后续若更新规范，可优先参考这些内容：

- OpenAI, *Harness engineering: leveraging Codex in an agent-first world*  
  https://openai.com/index/harness-engineering/

- Anthropic, *Effective harnesses for long-running agents*  
  https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

- Anthropic, *Harness design for long-running application development*  
  https://www.anthropic.com/engineering/harness-design-long-running-apps

- Anthropic, *Effective context engineering for AI agents*  
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

- Martin Fowler / Thoughtworks, *Harness engineering for coding agent users*  
  https://martinfowler.com/articles/harness-engineering.html

---

## 17. 一句话执行标准

后续每次开发都要先问一句：

> 这次我做的是 Agent 能力，还是 Agent + Harness？

只有同时补齐 Harness 的开发，才算完成。

