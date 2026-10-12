# API项目选择确认门禁一致性〔2026-10-12〕

基线e8d340cc。调研 `ApiProjectLocator._needs_clarification` 与 `ApiTestingModeRuntime._resolve_project`，发现同一规则存在两份实现：Locator会对“唯一未命名项目且用户无项目线索”要求确认，Runtime旧逻辑却对唯一候选直接自动选择。

修复Runtime对齐Locator：唯一候选只有在有正式名称或用户项目线索时自动选择；未命名候选进入 `awaiting_project_selection`，不能把主机名/临时标签当作正式项目确认。其他多候选分数差异规则保持。

实际项目身份/上传/解析/恢复和召回回归 **17 passed, 1 skipped, 3.72s**（owned目录）；没有业务HTTP或外部AI。仍未证明默认AI多候选排序质量，默认模型验收继续暂缓。
