# Docker 安全工具原始输出：受控响应头实测

本轮基线 `106a1114`。分类是“已有工具需实际验收，确认输出证据缺口后补实现”，并非新增扫描模式。

依据现有 `ToolRuntimeService._execute_security_runner`、`SecurityExecutionEnvironmentService.execute/_run_in_docker`、`HttpHeadersParser`、`FindingNormalizer.from_http_headers` 与 `evaluate_parser_success`，现有 `web-scan-runner/http_headers_probe` 已通过受控 Docker 命令、资源租约、退出码、解析器及 ToolJob 工件保存链路执行。`FindingNormalizer` 已要求有效 HTTP 状态码，因此连接失败不会生成缺失响应头发现；最初读代码时对此提出的风险已被完整代码和实测否定，不补重复校验。

`ArtifactStorageService.store_output_artifacts` 已支持本地文件到 RustFS，ToolJob 已把工件 metadata 保存在 PG，sessions 原件下载已校验提供的 SHA256 和字节数。采用现有链路保存完整流；不新建接口、数据库、配置或存储机制。现有审批/目标允许列表/工作分配检查保留。课程《Agent轨迹与协议鲁棒性》的“工具能调通但业务含义理解错”要求在此对应为核对真实响应与发现，不能只验容器启动或语法。

## 真实复现

新增门控 `test_security_evidence_live.py` 复用 `isolated_runtime`：测试所属 MySQL 配置表、PG 数据库、Redis 容器和 RustFS 对象；独立生产 main 启动并重启。使用当前已缓存 Kali 的完整 image ID `sha256:9520f0e11a5c74e80c758260e5ac2ad46bcae90fc0dda45670cb50b9f421c3d4`，不拉镜像、不安装软件；允许列表仅 `127.0.0.1`，按现有 Docker Desktop 传输重写访问测试所属本地服务。关闭 NET_RAW，无主动攻击或外部目标。

三次工具调用仅两次实际 GET：缺控制头与齐全控制头各一，另一调用访问关闭端口。添加 30,000 字符非敏感响应头验证大输出。实际缺失响应得到 4 项 baseline 发现，齐全 0，连接失败 exit1/failed/0发现。独立 API 重启后可读 PG 工件并下载 RustFS 转录，但两项大响应的原始输出均不完整，**1 failed in 28.45s**，原件与失败日志在 `p5-security-evidence-reproduction`。最初测试 import 路径写错，收集错误保存于 `p5-security-evidence-before`，该批没有执行功能。

根因：转录只保存 stdout 尾部20,000字符和 stderr尾部12,000字符，工具输出预览更短，响应头探针又不生成独立输出文件。解析时的完整内容未进入持久工件。改为额外保存完整捕获 stdout/stderr UTF-8文件（包含空流），加字节长度、SHA256和 `security-command-output-v1` metadata；仍沿现有统一工件上传、PG保存、会话下载路径。日志补解析终态、退出码、超时、发现数量及流长度，不输出响应内容。原转录和预览兼容保留，没有增大模型上下文。

## 最终实际验证

Agent_Server 内：

```text
RUN_LIVE_SECURITY_EVIDENCE=1
QA_SECURITY_EVIDENCE_DIR=G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p5-security-evidence-verified
..\.venv\Scripts\python.exe -m pytest tests/test_security_evidence_live.py -q --junitxml=../docs/upgrade_2026-10-03/p5-security-evidence-verified.xml
```

**1 passed in 27.78s，0 skipped**。9工件重启后真实下载；新完整流长度与PG metadata/hash一致，两项超过30KB响应头完整；失败stderr 3,800字节保存。终态回读结果一致且不增加GET，另一会话访问返回404。PG资源分配记录和3个完整容器ID的 `docker ps` 空结果保存；证明容器已删除，不声称 API 单进程已消费清理队列或完成容量复用。此测试调用现有 ToolRuntime，不调用 Agent/model，不运行前端报告或完整 App。

9个受影响模块最终 **111 passed in 10.62s**，Starlette/AnyIO既有弃用警告1条：执行环境、连续测试、授权、目标允许列表、能力、复现包API、工件存储、工具心跳和下载。第一批指定不存在的 `test_tool_job_service.py`，没有运行测试；第二批107通过4失败，均旧 `_Settings` 夹具无database导致默认PG任务库构造失败，与完整流逻辑无关。按同文件现有用法，在4个纯路径/后端测试中注入 `ToolJobService(InMemoryToolJobStore())`，保留原断言。失败日志不覆盖。

项目根实际 compileall 生产文件及两测试退出0。没有前端改动，未运行前端构建。安全完整Agent、其他扫描器/发现语义、超时/部分输出/存储故障组合和前端报告仍待验收；默认Step继续按用户决定待实名认证。完整P0—P5未完成。
