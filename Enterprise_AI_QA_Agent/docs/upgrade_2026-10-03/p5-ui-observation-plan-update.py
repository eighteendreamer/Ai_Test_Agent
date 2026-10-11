"""Synchronize the reviewed plan; preserve every previous trial and result."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

directory = Path(__file__).resolve().parent
paths = [directory / "PLAN.md",
    Path(r"C:\Users\32734\.codex\attachments\ec189608-bb79-41e6-925a-3d70ba54a017\已粘贴的文本.txt"),
    Path(r"C:\Users\32734\.codex\plans\01a1006b-4507-7e32-b83d-af8d1598e810\01a1009d-d8b2-7dc1-af17-13a77fada84d\PLAN.md")]
assert len({p.read_text(encoding="utf8") for p in paths}) == 1
positive = json.loads((directory / "p5-ui-observation-default6/positive/verification.json").read_bytes())
negative = json.loads((directory / "p5-ui-observation-default5/negative/verification.json").read_bytes())
assert positive["status"] == "passed" and negative["status"] == "failed"
assert all(p["terminal_extra_business_requests"] == p["terminal_extra_model_requests"] == 0 for p in (positive, negative))
suites = ET.parse(directory / "p5-ui-observation-final-rerun.xml").getroot().findall("testsuite")
assert sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites) == 0
assert sum(int(s.get("tests", 0)) - int(s.get("skipped", 0)) for s in suites) == 214
text = paths[0].read_text(encoding="utf8")
assert "### 7.37 " not in text
lines = text.splitlines()
lines[2] = "修订日期：2026-10-11；当前核对基线 `0907c39f`。Step认证已完成。本批持续浏览器eval保留原类型、原stdout和transcript，同步命令/证据根/确定性比较契约。真实默认UI简单正向live6正式passed，负向live5实际1对期望2正式failed；两项终态重投新增业务/模型请求均0，见7.37。正负分别在两批完成，不覆盖完整App、复杂场景、多候选排序/补全或全系统；live4/live5正项及历史失败均保留，只读纠正非流式超时仍需解决。最终214通过19门控跳过。普通需求文档默认生成质量、其余P0—P5保持active。"
text = "\n".join(lines) + "\n"
old = "人工DeepSeek验收尚未完成；本批自动化受控HTML验证不替代人工操作。"
assert text.count(old) == 1
text = text.replace(old, "DeepSeek公开页实际点击、落库和质量投影已补验，见7.22，业务完整性仍unknown；受控HTML补验不替代完整公开页面业务验收。")
old = "默认AI实际使用知识并完成操作/断言继续单独验收。"
assert text.count(old) == 1
text = text.replace(old, "默认AI固定场景读取及简单正/负正式断言见7.37；复杂场景和完整App继续单独验收。")
text += """
### 7.37 持续会话结构化观察、默认UI正负正式结果及终态〔2026-10-11〕

基线0907c39f。依据原native CLI/MCP、工具消息和持久证据根、CaseExecutionCheck校验及上批真实失败调研。eval在格式化前保存Playwright原类型，MCP与transcript直接传递result；原stdout/stderr不变。JSON.stringify仍是字符串，不猜类型，不多执行浏览器、不修正错误路径。公开工具及UI cases Prompt说明args单元素表达式、持续会话和独立evaluate_js区别，路径从result开始。原确定性比较只允许一个实际值的validator未变，字段description与公共契约补齐；普通Prompt v5、场景Prompt v6，历史请求/检查点不改。

| 实际验证 | 结果与边界 |
|---|---|
| 原缺口复现 | before夹具筛选原件空白格式错，保留失败；修正后reproduced在真实Chromium明确缺少result。false/0/null与同名字面字符串不能依靠stdout反推 |
| typed观察与回归 | 实际对象/数组/标量/null/空字符串/JSON.stringify/元素引用、同会话及transcript对账；早期31通过、后续117通过4跳过。最终214通过19门控跳过79.32秒，各批重叠不相加 |
| 默认live4 | 1失败119.96秒；知识、初始0与真实点击已发生，后续未引用command表达式被分成locator，原预算耗尽，正式error |
| 默认live5 | 1失败1通过462.86秒；负项期望2、实际1，正式failed且终态新增业务/模型请求均0；正项equals引用动作结果和计数两个值，校验正确拒绝，只读纠正3次HTTP超时，正式error |
| 新契约默认live6 | 正项1通过、1排除187.73秒；实际默认规划/执行CLI、生产API/清理CLI、MySQL/PG/Redis/RustFS，固定场景/别名→新页面初始0→点击一次→实际1→正式passed；终态新增业务/模型请求均0 |

四个运行的17份浏览器工件现场由API/RustFS下载并核对，持续命令stdout对应实际ToolJob。live5正项纠正原件另核SHA/size，响应只公共投影，清理仅本批拥有的2个对象。原默认8轮/8192、权限、模型接入与全局配置保留；没有新表/依赖/前端变动或新完整App构建声明。首次受控生命周期夹具无参数eval与历史Prompt恒等测试已按明确契约修订，失败日志保留；最终复跑通过。语法编译退出0是附加检查。

当前分类：**固定场景知识读取及简单默认UI正/负正式断言与终态重投，已按上述跨批范围实际验证**。**已有实现、尚需验收**：多候选项目召回/排序/补全/执行、复杂场景/其他模式/完整App、普通需求文档默认生成质量与模块恢复、广业务影响评审/环境适用性/生产部署。模板/Excel/JSON完整App专项已有6.87，不再将其列为完全未做。DeepSeek公开页采集已见7.22，完整业务质量仍未完成。

**仍需补实现或依据真实缺口补强**：只读纠正阶段固定传输策略（非流式超时仍复现；既有generation_pipeline.invoke_frozen和ModelRuntimeService.stream_handler可作依据，必须冻结新阶段策略并保留旧恢复，不能按供应商硬编码）；准备生命周期/unknown只读查证/执行中修订，selector修复评审/失败定位与复杂响应因果，API自动续期/Cookie/并发刷新，持续性能容量/AI流式阶段指标、安全故障组合，全阶段SDK/HTTP恢复与生产历史迁移投影。两份旧Worker原件仍未恢复。完整P0—P5保持active；没有用本批简单UI取代其余目标。

调研、原件与审计见P5_UI_OBSERVATION_RESEARCH.md、p5-ui-observation-*及IMPLEMENTATION.md；三份方案同步。仅提交本批白名单，不推送/PR。
"""
raw = text.replace("\n", "\r\n").encode("utf8")
for path in paths: path.write_bytes(raw)
assert len({p.read_bytes() for p in paths}) == 1
implementation = directory / "IMPLEMENTATION.md"
content = implementation.read_text(encoding="utf8")
assert "## 2026-10-11 持续会话观察" not in content
content += """

## 2026-10-11 持续会话观察与默认UI正负正式结果

基线0907c39f。native eval保存格式化前原类型，MCP和transcript透传result，原stdout保持。同步工具/UI cases的表达式参数与证据根说明；CaseExecutionCheck既有单值比较条件同步公开Schema/Prompt，普通v5、场景v6。旧回执校验、原8轮/8192、全局模型配置与业务历史保留，无新表/依赖/前端变动。

真实默认live4 1失败119.96秒；live5 1失败1通过462.86秒，负向实际1/期望2正式failed且终态零新增业务/模型发送；live6正向1通过1排除187.73秒，真实固定场景→新页面0→物理点击一次→当前1→正式passed，终态也零新增。两批范围分别记录，正项live5仍因多值比较与纠正超时error，未改判。17份浏览器工件API/RustFS/持久stdout核对；纠正2原件SHA/size验证并仅清理owned对象，响应只公共投影。

最终十九模块214通过19门控跳过79.32秒，含实际Chromium及真实失败样本；较早批次重叠不累加。初次历史Prompt恒等断言与受控无参数eval夹具已按新契约修订，失败原件保留。compileall退出0。没有新增完整App/前端构建、复杂UI或其他模式通过声明。

三方案同步7.37，旧原件/保护文件/秘密及字节核对另见p5-ui-observation-validation.json。本批中文白名单commit，不推送/PR。完整目标仍active；下一项只读纠正传输需要先沿既有冻结生成/stream_handler调研，其他未完成分类见方案7.37。
"""
implementation.write_bytes(content.replace("\n", "\r\n").encode("utf8"))
print("Three plans synchronized to 7.37; positive/negative scope verified")
