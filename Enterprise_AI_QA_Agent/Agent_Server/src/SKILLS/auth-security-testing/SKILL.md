---
name: auth-security-testing
description: 审查和规划 JWT、Session、OAuth、密码、对象级授权、权限提升、速率限制和认证绕过测试。用于安全测试、API 安全与代码审批；动态验证必须经过目标授权和风险审批。
---

# 认证与授权安全测试

先建立身份、角色、资源和动作矩阵，再验证认证与授权边界。静态审查可直接进行；登录尝试、凭证测试、token 操作和越权验证必须通过安全模式 Runner，并继承已验证目标范围。

## 检查范围

- Session/JWT 的签名、过期、刷新、撤销、固定、重放和存储位置。
- OAuth/OIDC 的 state、PKCE、redirect URI、scope、audience 和 issuer。
- 对象级与功能级授权、租户隔离、默认拒绝和管理员边界。
- 密码策略、账户枚举、锁定、速率限制和错误信息。
- Cookie 的 Secure、HttpOnly、SameSite 与 CSRF 边界。

## 执行纪律

1. 确认授权目标、允许身份、测试账号和最大尝试次数。
2. 优先使用低风险读取验证；高风险凭证攻击必须单独审批。
3. 保存请求、响应、角色、资源和预期矩阵，过滤 token/密码。
4. 区分认证失败、授权失败、环境失败和工具失败。
5. 不自动扩大账号、租户、域名、端口或回调范围。

## 身份与授权矩阵

列出 actor、认证状态、角色/租户、资源 owner、动作、预期结果和证据，至少包含匿名、普通用户、自有资源、他人资源、管理员、过期 token、错误 audience/scope 和禁用账户。JWT 检查算法/签名、issuer/audience、过期/刷新/撤销、重放和存储；Session/Cookie 检查固定、轮换、注销、Secure/HttpOnly/SameSite 和 CSRF；OAuth/OIDC 检查 state、nonce、PKCE、redirect URI、scope 和账户关联。

## 动态执行门禁

安全 Runner 只接收已授权 target、账号 ref、最大尝试次数和禁止范围；不得把凭据写入 prompt、日志或 artifact。失败区分认证拒绝、授权拒绝、目标环境、工具和策略阻断。需要 JWT、IDOR、Playwright、Session、密码、OAuth、速率限制和报告细节时读取 `references/source-1.md`。
