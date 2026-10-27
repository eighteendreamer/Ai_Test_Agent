# 连尚孵化器申请资料复用

个人资料、表单快照、视频放在仓库外。此目录只放通用脚本及合成数据测试，不放姓名、联系方式、生日、履历或登录凭据。

## 使用

在项目根目录用已有 `.venv/Scripts/python.exe` 运行：

```powershell
# 默认仅检查本地资料，不联网、不打开浏览器
.\.venv\Scripts\python.exe scripts/application_forms/fill_linkincubator.py --data "私有资料目录/application.private.json"
# 辅助填写：手动登录，确认 FILL 后才将资料填入申请页
.\.venv\Scripts\python.exe scripts/application_forms/fill_linkincubator.py --data "私有资料目录/application.private.json" --fill
# 单元测试（合成数据，不连接申请网站）
.\.venv\Scripts\python.exe -m unittest discover -s scripts/application_forms -p "test_*.py" -v
```

默认使用已安装 Edge，可用 `--channel chrome` 或 `--channel chromium` 切换；不自动安装浏览器或依赖。Playwright 复用项目现有依赖。

## 行为与限制

- 只填已保存的非空字段，不清除网站已有内容；按字段 `name` 匹配，不依赖动态 ID。
- 下拉框通过实际选项点击，而非只修改显示值；读取控件值验证结果。字段/选项变化会报告，不猜填。
- 打开全新临时浏览器，不复用或导出登录状态，不记录字段内容。字段状态报告保存在私有资料目录。
- 地址限定当前 c23 申请页，不自动适配其他期次。网站改版需重新核对映射。
- 无提交或文件上传逻辑，视频需人工上传，最终提交由本人核对操作。
- 推荐人仅保存为单独指示；当前表单未发现单独推荐人控件，不伪造字段。
- 超字数原文保留并告警，经历描述、工作时间及近期进展提交前需更新。
- 单元测试覆盖资料校验、文本/下拉框分支、空值、缺字段、幂等及地址变化；没有在真实网站执行整表回填，避免不必要地再次传输个人资料。

## 依据

字段与步骤结构来自用户授权读取的当前申请页 DOM（2026-10-27）；API 对照 [Playwright Locator](https://playwright.dev/python/docs/api/class-locator)、[BrowserType](https://playwright.dev/python/docs/api/class-browsertype)。不访问页面内部 Vue 状态，不调用私有提交接口。
