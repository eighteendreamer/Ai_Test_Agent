# 前端与桌面依赖公告处置记录

检查日期：2026-10-01；起点：`main@f1f2277`；环境：Windows x64、Node 22.22.1、npm 10.9.4。

## 证据口径

`npm audit` 表示锁定版本命中公开公告，不证明产品输入可以触发公告中的行为。本轮没有执行破坏性、拒绝服务或外网利用样本。以下可达性判断来自正式代码、依赖调用点与维护者公告；未做攻击验证的项不表述为动态复现。

原始全量审计为 **15 个依赖条目：7 high、8 moderate**。处置后为 **6 个：3 high、3 moderate**，全量审计仍退出 1。`npm audit --omit=dev --json` 处置后 **0 个、退出 0**；但 Electron 虽列为 devDependency，二进制实际进入桌面发行包，因此生产 npm 审计为零不代表桌面运行时安全。

原始 JSON 留在本机 `artifacts/dependency-audit/`（Git 忽略），包含 `before-2026-10-01.json`、`after-2026-10-01.json`、`production-after-2026-10-01.json`、依赖树及浏览器报告；公开复核依据是本记录、Git 版本与 package-lock。审计数据库随时间变化，后来执行不保证条目数量完全相同。

## 已处置范围

| 依赖 | 原锁定版本 → 新锁定版本 | 项目调用与结论 |
| --- | --- | --- |
| markdown-it | 14.3.0 → 14.3.2 | `src/utils/markdown.ts` 正式聊天渲染开启 `linkify: true`，调用条件符合 [维护者公告 GHSA-253c-mchw-3w2r](https://github.com/advisories/GHSA-253c-mchw-3w2r)，采用修复版本；未声称捕获实际用户卡死。报告 inline renderer 的 linkify 为 false。 |
| mermaid | 11.15.0 → 11.17.2 | 用于 VitePress 文档及图生成，未发现聊天渲染器消费 Mermaid；包含 [11.16.1 的 CSS 注入等修复](https://github.com/mermaid-js/mermaid/security/advisories/GHSA-6x64-9x62-f2gx)。未证明所有公告前提在项目中成立。 |
| dompurify | 3.4.7 → 3.4.16 | Mermaid 的传递依赖；未发现项目调用 IN_PLACE、自定义 hook 等 [相关公告前提](https://github.com/advisories/GHSA-55q2-fjhq-7xh7)，仍升级同一主版本修复版本。 |
| vite（应用） | 6.4.2 → 6.4.3 | 应用默认绑定 127.0.0.1；[Windows fs.deny 公告](https://github.com/advisories/GHSA-fx2h-pf6j-xcff)要求开发服务暴露。更新现有主版本；没有声称生产静态 HTTP 服务就是 Vite 开发服务。 |
| brace-expansion | 2.1.4 → 2.1.7 | Vue 测试工具的 js-beautify/editorconfig/minimatch 链，[递归 DoS](https://github.com/advisories/GHSA-qhr7-859c-m2p7)及 [二次复杂度](https://github.com/advisories/GHSA-q2hr-2g5m-vwhr)修复；不是正式聊天输入链路。 |
| nanoid | 3.3.11 → 3.3.19 | PostCSS 传递依赖，采用 [3.3.18 修复之后的版本](https://github.com/advisories/GHSA-2v37-7h3g-55p8)；未把负数、自定义生成器等公告前提假定为产品输入。 |
| postcss | 8.5.9 → 8.5.28 | Vue/Vite/VitePress 构建链；采用 [source map 修复版本](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp)，未证明模型正文进入 CSS/source map 解析。 |
| undici | 7.29.0 → 7.30.0 | jsdom 测试依赖，采用 [7.29.1 安全修复之后的版本](https://github.com/advisories/GHSA-3xpg-4rpp-hhhm)；不是 Python 模型客户端。 |
| node-notifier / 私有 uuid | 删除未使用的 node-notifier 10.0.1 / uuid 8.3.2 | Git 源码及本机 src/electron/scripts/tests/e2e/docs 检索无消费方。通知实际使用 `desktopNotifications.ts` 的 preload bridge/浏览器 Notification，main.js 使用 Electron 原生 Notification。uuid 公告只涉及 v3/v5/v6 的外部 buffer，旧 notifier 调用 v4，不成立为已复现产品漏洞；删除闲置依赖，未采用审计建议的 notifier 大版本降级。 |

直接依赖最低版本同步到 markdown-it `^14.3.2`、Mermaid `^11.16.1`、Vite `^6.4.3`。根据 [npm update 官方说明](https://docs.npmjs.com/cli/v10/commands/npm-update/)，具名更新遵循父依赖 semver；没有执行 audit fix --force、peer 忽略或覆盖。Mermaid 更新带来 parser 1.2.1、cytoscape 3.34.3 及 fastdom/strictdom；逐项 `npm view` 确认版本、许可证及 engines。除 DOMPurify 的 MPL-2.0 OR Apache-2.0 外，这批包均 MIT；Node 22.22.1 满足查询到的 engines。

## 仍未关闭

- **Electron 31.7.7 / extract-zip 2.0.1**：发行脚本复制 electron/dist；contextIsolation/sandbox 为 true、nodeIntegration 为 false，未配置 offscreen 或 nodeIntegrationInWorker，但这些设置不足以撤销所有 Electron 公告。extract-zip 位于下载/安装链，不是模型上传文件的解压接口。需要受支持运行时升级及真实 Windows 主进程、preload、录制、窗口和打包兼容验证。本轮没有升级 Electron 或声称桌面全部安全。
- **VitePress 1.6.4 / 内嵌 Vite 5.4.21、esbuild 0.21.5 / Mermaid 文档插件**：npm 检查稳定版仍为 1.6.4，next 为 2.0.0-alpha.20，未将正式工具链盲目迁移到 alpha，也未强制覆盖内部 Vite 主版本。顶层 Vite 的补丁不修复内嵌副本。[esbuild 公告](https://github.com/evanw/esbuild/security/advisories/GHSA-67mh-4wv8-2f99)涉及自身 serve API，不能仅因构建中用 esbuild 就宣称公告可达；文档开发服务的 Vite 告警仍待上游/兼容性处置。默认 dev-all 会启动文档开发服务，需单独评估其使用条件。

## 实际验证

- 更新、删闲置依赖后 `npm ls --all --json`：退出 0，无依赖树错误。
- 前端全套：**35 文件、143 passed**（更新后及删除闲置依赖后分别执行）。
- `npm run build`：严格 Vue/TS 检查和正式 Web 构建通过。
- `npm run desktop:build`：严格 Vue/TS、桌面 renderer 和 VitePress 文档构建通过；文档仍有 >500kB chunk 告警。此命令在删除闲置 notifier 前执行；删除后正式 Web 构建与浏览器探针重新通过，未将 renderer 构建当成 Electron 二进制启动验证。
- 正式生产产物在 Chromium **151.0.7922.34** 的 **2 场景通过**：语言懒加载/保存恢复、聊天公式与高亮；pageerror/未知 API 为零。使用隔离成功 API 夹具，不是实际模型、邮件或后端联合 E2E。
- 首次批量 npm update 在 npm 10.9.4 arborist peer 解析中报 `Cannot read properties of null (reading 'edgesOut')`，没有生成新 lock；改为先显式安装已提升下限的直接依赖、再具名更新传递依赖成功。该工具错误未登记为产品缺陷。

P1-81 保持部分修复。后端依赖、收费模型、生产部署、拒绝服务利用、Electron GUI 和完整发行包没有在这一步验证。
