# P3.5 本地 Git 文档来源：查证、实现与验收

研究基线：`bf84f84`，2026-10-04。完整 P0—P5 保持原范围；本节只细化可选 Git 来源，上传无需 Git。

**实施状态：服务/配置/API/前端已接通，真实专项 25 通过，MD/DOCX 正式 App 完整产品 2 通过。** 采用做法及验收目标见下文，失败与最终证据见 IMPLEMENTATION.md；生产历史迁移和其他格式产品流程未执行，P4/P5 与其余范围未完成。

| 依据 | 采用做法 |
|---|---|
| `code_review_mode/project_source.py` 接收自由 `root_path`，governance/runtime 通过 subprocess 读 Git | 仅借鉴本地读取。新增项目所属仓库配置，不能把自由路径或其他项目的登记当作授权依据 |
| `api_docs_service.py` 的上传/版本/原件扫描/幂等/提交回查/补偿；PG 目录将完整记录、版本和提交保存为 JSON | Git 读取后复用同一入库链。来源由服务端内部参数传入，公开普通上传 DTO 不接收伪造来源；增加可选来源字段，不改旧上传请求哈希 |
| MySQLApplicationConfigStore 的项目 scope/revision，项目 require_active | 仓库 ID、名称、服务器本地根目录、启用状态和读取预算存 MySQL。仓库列表不从全局或其他项目继承。保存/新导入要求活动项目；已保存提交与历史仍可读 |
| LimitedParserProcess 的固定内部入口、超时/CPU/RSS/输出/取消与后代回收 | 增加内部 Git 读取适配器与固定 Worker，复用生命周期，不复制进程监控。Git 子进程使用参数数组，保留返回码及 stdout/stderr；原始 blob 字节作为读取结果留存 |
| Git 官方 [rev-parse](https://git-scm.com/docs/git-rev-parse)、[ls-tree](https://git-scm.com/docs/git-ls-tree)、[cat-file](https://git-scm.com/docs/git-cat-file)、[git](https://git-scm.com/docs/git) | `--verify --end-of-options ref^{commit}` 固定 commit；`--full-tree -z` 查精确路径及 mode/type/blob；先查 blob 大小，再读原始字节，不使用 textconv/filter。全局 literal-pathspecs/no-replace-objects/no-lazy-fetch/no-optional-locks，缺少能力明确失败 |

流程为：项目登记仓库 → 输入引用与仓库内相对路径 → 解析为完整 commit/blob 及配置修订 → 明确新文档或已有文档新版本 → 读取固定原件并复用保存服务 → 已有解析/比较入口。提交只接受完整 commit/blob，不能提交可移动的 HEAD/分支作为最终输入。新版本明确 document_id 和 expected_parent_version_id，不按文件名合并。

来源保留仓库 ID/名称/已核对根目录、配置修订、完整 commit/blob、路径、mode、字节大小/SHA256、Git 版本与读取策略。Git blob 身份和普通 SHA256分别保存。来源是当时读取事实，后续改配置不改写历史。相同字节不新增版本时，原版本来源保留，本次 Git 来源仍随提交结果保存；不能把新的 commit 来源塞进旧版本。

Git 提交的原始请求签名随现有 PG ApiDocSubmission 保存。入口先查同请求；一致则返回固定原结果，不依赖当前仓库仍在、启用、配置未改或项目仍活动。参数不同则冲突。首次导入和更新都复用现有目录/资产/版本事务与补偿，不另建 submission 表，不吞掉无法确认的数据库结果。

根目录由设置验证为准确仓库根；支持普通仓库和 bare，子目录不能默默向上发现其他仓库。路径只能是仓库内明确相对文件，Git magic/glob按字面处理；拒绝目录、submodule和符号链接，不读取工作区。禁用替换对象和缺失对象按需抓取，读取不会 clone/fetch/checkout/commit，不执行用户仓库工具或修改 index/refs。Git LFS pointer首期明确报能力缺口，不能把pointer正文当原件。

验收先用真实临时仓库及两次commit，保留未提交/已暂存修改，导入旧commit应逐字节相同；核对HEAD/index/refs/工作区和所有已有对象读取前后不变。覆盖中文/空格/字面特殊字符、二进制DOCX、路径/引用错误、size/CPU/timeout/取消、replace对象、缺失对象及bare。再用真实MySQL/PG/RustFS和公开路由验证新建/明确更新/同字节/回执丢失/服务重建/配置移除后的重放、错误项目、归档及事务补偿。解析/比较使用现有独立CLI继续验收来源输入，普通上传回归。

后端/契约通过后接正式前端仓库设置、引用解析与导入，并验收完整App中的配置→提交→回执丢失→恢复→解析/比较。后端专项不冒充完整产品交付；同仓库的附加Git diff是可选补充，不替代固定解析差异。P4需求匹配/影响/追踪另实施。


## 本轮实际结果（研究基线 5500367）

- 新增 GitDocumentService、项目配置/resolve/import/submissions 路由及生产容器/入口接线；没有新的版本或 submission 表。GitDocumentOrigin 保存在现有版本/提交 JSON；普通上传不携带来源参数，旧请求哈希不变。内部和事务重放同时核对来源请求签名。
- 请求身份沿用现有项目/文档/请求组合；未提供 document_id 的首次导入使用项目和请求派生的稳定文档 ID。已有文档更新明确携带 document_id 和 expected_parent_version_id，不宣称仅凭 request_id 就跨所有文档全局唯一。
- 同字节不同 commit/path 不改当前版本来源，本次来源留在提交结果；配置变化拒绝新提交，原请求先查已存结果，不重新读取 Git。改绑项目后旧项目不能重放读取。
- DocumentGitImport 复用文档页面：独立新建与明确追加分别挂接；缓存完整固定请求参数而非原件/凭据。恢复历史提交后另读当前目录，避免把当前 V2 回退为原提交 V1。只读项目保留原提交查询。
- 真实专项 25 通过；扩大回归 132 通过；二进制超时日志不输出正文的回归也通过，完整命令见 IMPLEMENTATION。前端相关 58 通过，原生表单有效性回归通过。两个正式 App 场景分别用 MD 和 DOCX，生产 API/独立解析比较 CLI/真实存储/Chromium 均实际运行，2 通过（109.00 秒），模型调用 0，仓库字节快照不变，当前目录仍为 V2。
- 失败证据保留：最初 fixture 的 autocrlf 改写测试提交字节；只读 Git 对象删除/文档更新调用签名错误修正夹具。真实页面先复现菜单遮罩，再复现 number 输入 min/step 冲突，随后重新进入的小弹窗裁剪菜单；采用原生项目选择、正确秒数/整数字节约束与原有模态头/内容结构，未加 force click、重置滚动或业务特判。
