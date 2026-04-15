"""
浏览器工具实现层

基于 Selenium WebDriver 实现全部 UI 探索工具。
独立运行，不依赖 browser-use 或任何第三方 Agent 框架。

工具分层（参考 Claude Code 架构）：
  基础工具 (16): navigate, screenshot, read_page, find, click, type, scroll, hover,
                 evaluate, get_links, get_forms, get_buttons, get_inputs,
                 select_dropdown, wait
  高级工具 (3):  scan_page — 一键扫描页面全部交互项
                 probe_interactions — 逐项探测交互效果
                 auto_login — 程序化登录
"""

import json
import logging
import base64
import os
import time
import hashlib
from typing import Any, Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 安全常量（高级工具使用）
# ═══════════════════════════════════════════════════════════════════

DANGEROUS_KEYWORDS = {
    "删除", "移除", "清空", "重置", "注销", "退出登录", "logout", "delete",
    "remove", "clear", "drop", "destroy", "提交订单", "确认支付",
    "发布", "publish", "submit", "确认删除",
}

SKIP_KEYWORDS = {
    "loading", "spinner", "copyright", "版权", "备案",
}

PROBE_WAIT_SECONDS = 1.5
PAGE_LOAD_WAIT = 2.0
INTERACTION_TIMEOUT = 10
MAX_INTERACTIONS_PER_PAGE = 40

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    screenshot_b64: Optional[str] = None  # 附带截图（base64）

    def to_dict(self) -> dict:
        result = {"success": self.success}
        if self.output is not None:
            result["output"] = self.output
        if self.error:
            result["error"] = self.error
        if self.screenshot_b64:
            result["screenshot"] = True
        return result


class BrowserTools:
    """
    全部浏览器工具集合

    提供 16 种工具，对应 prompts.py 中 TOOL_DEFINITIONS 的定义。
    所有工具统一通过 Selenium WebDriver 执行。
    """

    def __init__(self, driver):
        """
        Args:
            driver: Selenium WebDriver 实例
        """
        self.driver = driver
        # 截图默认保存路径
        self._screenshot_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "save_floder", "Screenshot"
        )
        os.makedirs(self._screenshot_dir, exist_ok=True)
        self._highlight_injected = False

    # ─── 视觉高亮（browser-use 风格）──────────────────────────────

    def _inject_highlight_css(self):
        """注入高亮动画 CSS（仅一次）"""
        if self._highlight_injected:
            return
        try:
            self.driver.execute_script("""
            (function(){
                if(document.getElementById('__ui_explore_highlight_css')) return;
                var style = document.createElement('style');
                style.id = '__ui_explore_highlight_css';
                style.textContent = `
                    @keyframes __ui_explore_pulse {
                        0% { box-shadow: 0 0 0 3px rgba(255,100,100,0.8); }
                        50% { box-shadow: 0 0 0 6px rgba(255,100,100,0.4); }
                        100% { box-shadow: 0 0 0 3px rgba(255,100,100,0.0); }
                    }
                    @keyframes __ui_explore_fade {
                        0% { opacity: 1; }
                        70% { opacity: 1; }
                        100% { opacity: 0; }
                    }
                `;
                document.head.appendChild(style);
            })();
            """)
            self._highlight_injected = True
        except Exception:
            pass

    def _highlight_element(self, selector: str, color: str = "#ff4444"):
        """高亮页面上的元素（3秒后自动消除）"""
        try:
            self._inject_highlight_css()
            self.driver.execute_script("""
            (function(sel, color){
                try {
                    var el = document.querySelector(sel);
                    if(!el) return;
                    var orig = el.style.cssText;
                    el.style.outline = '3px solid ' + color;
                    el.style.outlineOffset = '2px';
                    el.style.animation = '__ui_explore_pulse 0.6s ease-in-out 2, __ui_explore_fade 3s forwards';
                    setTimeout(function(){
                        el.style.cssText = orig;
                    }, 3000);
                } catch(e){}
            })(arguments[0], arguments[1]);
            """, selector, color)
        except Exception:
            pass

    def _highlight_coordinate(self, x: int, y: int, color: str = "#ff4444"):
        """在坐标位置显示点击标记（圆圈脉冲 + 消退）"""
        try:
            self._inject_highlight_css()
            self.driver.execute_script("""
            (function(x, y, color){
                var dot = document.createElement('div');
                dot.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;' +
                    'width:24px;height:24px;border-radius:50%;border:3px solid '+color+';' +
                    'left:'+(x-12)+'px;top:'+(y-12)+'px;' +
                    'animation:__ui_explore_pulse 0.6s ease-in-out 2, __ui_explore_fade 3s forwards;';
                document.body.appendChild(dot);
                setTimeout(function(){ dot.remove(); }, 3000);
            })(arguments[0], arguments[1], arguments[2]);
            """, x, y, color)
        except Exception:
            pass

    def set_screenshot_dir(self, directory: str):
        """设置截图保存目录"""
        self._screenshot_dir = directory
        os.makedirs(directory, exist_ok=True)

    # ─── 导航类工具 ──────────────────────────────────────────────

    def navigate(self, url: str, timeout: int = 30) -> ToolResult:
        """导航到指定 URL"""
        try:
            self.driver.set_page_load_timeout(timeout)
            self.driver.get(url)
            # 等待 DOM ready
            time.sleep(1)
            current_url = self.driver.current_url
            title = self.driver.title
            return ToolResult(
                success=True,
                output={
                    "url": current_url,
                    "title": title,
                    "message": f"已导航到 {current_url}"
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=f"导航失败: {e}")

    def wait(self, seconds: float = 2, selector: str = None) -> ToolResult:
        """等待页面或特定元素"""
        try:
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By

            if selector:
                WebDriverWait(self.driver, max(seconds, 5)).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                return ToolResult(
                    success=True,
                    output=f"元素 {selector} 已出现"
                )
            else:
                time.sleep(seconds)
                return ToolResult(
                    success=True,
                    output=f"已等待 {seconds} 秒"
                )
        except Exception as e:
            return ToolResult(success=False, error=f"等待超时: {e}")

    # ─── 信息获取类工具 ──────────────────────────────────────────

    def screenshot(self, full_page: bool = False, filename: str = None) -> ToolResult:
        """截取页面截图"""
        try:
            if full_page and hasattr(self.driver, 'execute_cdp_cmd'):
                # 全页截图 (Chrome CDP)
                self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
                    "width": self.driver.execute_script("return document.body.scrollWidth"),
                    "height": self.driver.execute_script("return document.body.scrollHeight"),
                    "deviceScaleFactor": 1,
                    "mobile": False
                })
                img_data = self.driver.get_screenshot_as_png()
                self.driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
            else:
                img_data = self.driver.get_screenshot_as_png()

            b64 = base64.b64encode(img_data).decode()

            # 保存到文件
            fname = filename or f"screenshot_{int(time.time())}"
            if not fname.endswith('.png'):
                fname += '.png'
            fpath = os.path.join(self._screenshot_dir, fname)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, 'wb') as f:
                f.write(img_data)

            return ToolResult(
                success=True,
                output={
                    "size_kb": len(img_data) // 1024,
                    "full_page": full_page,
                    "saved_to": fname
                },
                screenshot_b64=b64,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"截图失败: {e}")

    def read_page(self, include_dom: bool = True, max_length: int = 15000) -> ToolResult:
        """读取页面完整信息"""
        try:
            url = self.driver.current_url
            title = self.driver.title

            # 可见文本
            try:
                body = self.driver.find_element("tag name", "body")
                visible_text = body.text[:max_length]
            except Exception:
                visible_text = ""

            # DOM 结构摘要
            dom_info = None
            if include_dom:
                dom_info = self.driver.execute_script("""
                    // 获取页面结构摘要
                    function summarizeDOM(root, depth=0) {
                        if (depth > 3) return '';
                        let result = '';
                        for (let node of root.children) {
                            let tag = node.tagName.toLowerCase();
                            let id = node.id ? '#' + node.id : '';
                            let cls = node.className && typeof node.className === 'string'
                                ? '.' + node.className.split(/\s+/).slice(0,2).join('.') : '';
                            let text = node.textContent.trim().substring(0, 30);
                            let childrenCount = node.children.length;
                            result += '  '.repeat(depth) + tag + id + cls;
                            if (childrenCount > 0) {
                                result += ' (' + childrenCount + ' children)\\n';
                                result += summarizeDOM(node, depth + 1);
                            } else if (text) {
                                result += ': "' + text.replace(/\\n/g, ' ') + '"\\n';
                            } else {
                                result += '\\n';
                            }
                        }
                        return result;
                    }
                    return summarizeDOM(document.body);
                """)[:max_length]

            # meta 信息
            meta_info = self.driver.execute_script("""
                return {
                    viewport: {width: window.innerWidth, height: window.innerHeight},
                    scrollHeight: document.documentElement.scrollHeight,
                    formCount: document.forms.length,
                    linkCount: document.links.length,
                    inputCount: document.querySelectorAll('input,textarea,select').length,
                    buttonCount: document.querySelectorAll('button,[role="button"]').length,
                    tableCount: document.tables ? document.tables.length : document.querySelectorAll('table').length,
                    hasReact: !!document.querySelector('[data-reactroot],#__next,#root[data-reactroot]'),
                    hasVue: !!document.querySelector('[data-v-]'),
                    hasAngular: !!document.querySelector('[ng-src],[ng-controller],src-root'),
                    hasjQuery: typeof jQuery !== 'undefined',
                };
            """)

            return ToolResult(
                success=True,
                output={
                    "url": url,
                    "title": title,
                    "visible_text": visible_text[:max_length],
                    "dom_structure": dom_info,
                    "meta": meta_info,
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=f"读取页面失败: {e}")

    def find(self, query: str, by: str = "css") -> ToolResult:
        """查找页面元素"""
        try:
            from selenium.webdriver.common.by import By
            by_map = {"css": By.CSS_SELECTOR, "xpath": By.XPATH, "text": By.CSS_SELECTOR}
            locator = by_map.get(by, By.CSS_SELECTOR)

            if by == "text":
                # 文本查找用 XPath contains
                from selenium.webdriver.common.by import By
                elements = self.driver.find_elements(
                    By.XPATH,
                    f"//*[contains(text(), '{query}')]"
                )
            else:
                elements = self.driver.find_elements(locator, query)

            results = []
            for i, el in enumerate(elements[:20]):  # 最多返回20个
                info = self._element_info(el)
                info["index"] = i
                results.append(info)

            return ToolResult(
                success=True,
                output={
                    "query": query,
                    "by": by,
                    "count": len(elements),
                    "elements": results[:20],
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=f"查找元素失败: {e}")

    def get_links(self, filter_external: bool = False) -> ToolResult:
        """获取页面所有链接"""
        try:
            from urllib.parse import urlparse
            current_domain = urlparse(self.driver.current_url).netloc

            links = []
            for el in self.driver.find_elements("tag name", "a"):
                href = el.get_attribute("href") or ""
                text = el.text.strip()
                if not href or not text:
                    continue
                if filter_external:
                    link_domain = urlparse(href).netloc
                    if link_domain and link_domain != current_domain:
                        continue
                links.append({
                    "href": href,
                    "text": text[:100],
                    "is_external": urlparse(href).netloc != current_domain if href else False,
                })

            return ToolResult(
                success=True,
                output={"links": links, "total": len(links)}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"获取链接失败: {e}")

    def get_forms(self) -> ToolResult:
        """获取页面所有表单"""
        try:
            forms = []
            for form_el in self.driver.find_elements("tag name", "form"):
                form_data = {
                    "action": form_el.get_attribute("action") or "",
                    "method": form_el.get_attribute("method") or "GET",
                    "id": form_el.get_attribute("id") or "",
                    "name": form_el.get_attribute("name") or "",
                    "fields": [],
                }

                # 表单内所有输入字段
                for inp in form_el.find_elements("css selector", "input,textarea,select,button[type=submit]"):
                    field = {
                        "tag": inp.tag_name,
                        "type": inp.get_attribute("type") or inp.tag_name,
                        "name": inp.get_attribute("name") or "",
                        "id": inp.get_attribute("id") or "",
                        "placeholder": inp.get_attribute("placeholder") or "",
                        "required": inp.get_attribute("required") is not None,
                        "selector": self._get_selector(inp),
                    }
                    if inp.tag_name == "select":
                        options = []
                        for opt in inp.find_elements("tag name", "option"):
                            options.append({"value": opt.get_attribute("value"), "text": opt.text.strip()})
                        field["options"] = options
                    form_data["fields"].append(field)

                forms.append(form_data)

            return ToolResult(
                success=True,
                output={"forms": forms, "total": len(forms)}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"获取表单失败: {e}")

    def get_buttons(self) -> ToolResult:
        """获取页面所有按钮（包括滚动容器中不在视口内的按钮）"""
        try:
            buttons = []
            seen_selectors = set()
            for sel in ['button', '[role="button"]', 'input[type="submit"]',
                        'input[type="button"]', 'input[type="reset"]',
                        '[class*="btn"]', 'a[class*="btn"]']:
                for btn in self.driver.find_elements("css selector", sel):
                    selector = self._get_selector(btn)
                    if selector in seen_selectors:
                        continue
                    # 使用宽松可见性检测：元素在 DOM 中且有非零尺寸即可
                    if not self._is_interactable(btn):
                        continue
                    seen_selectors.add(selector)
                    info = self._element_info(btn)
                    info.pop("rect", None)
                    buttons.append(info)

            return ToolResult(
                success=True,
                output={"buttons": buttons, "total": len(buttons)}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"获取按钮失败: {e}")

    def get_inputs(self) -> ToolResult:
        """获取页面所有输入框"""
        try:
            inputs = []
            seen_selectors = set()
            for sel in ['input:not([type=hidden])', 'textarea', 'select',
                        '[contenteditable=true]', '[contenteditable="true"]']:
                for inp in self.driver.find_elements("css selector", sel):
                    selector = self._get_selector(inp)
                    if selector in seen_selectors or not inp.is_displayed():
                        continue
                    seen_selectors.add(selector)
                    info = self._element_info(inp)
                    info.pop("rect", None)
                    inputs.append(info)

            return ToolResult(
                success=True,
                output={"inputs": inputs, "total": len(inputs)}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"获取输入框失败: {e}")

    # ─── 交互类工具 ──────────────────────────────────────────────

    def _find_element(self, selector: str):
        """智能查找元素：自动识别 XPath vs CSS 选择器"""
        from selenium.webdriver.common.by import By
        if selector.startswith('//') or selector.startswith('(//') or selector.startswith('./'):
            return self.driver.find_element(By.XPATH, selector)
        return self.driver.find_element(By.CSS_SELECTOR, selector)

    def click(self, selector: str = None, coordinate: list = None, wait_after: bool = True) -> ToolResult:
        """点击元素"""
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.action_chains import ActionChains

            if coordinate:
                self._highlight_coordinate(coordinate[0], coordinate[1], '#ff6644')
                actions = ActionChains(self.driver)
                actions.move_by_offset(coordinate[0], coordinate[1]).click().perform()
                msg = f"已在坐标 ({coordinate[0]}, {coordinate[1]}) 点击"
            elif selector:
                el = self._find_element(selector)
                # 滚动到可见区域
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.3)
                # XPath 选择器无法用于 CSS 高亮，改用 JS 直接高亮元素
                try:
                    self.driver.execute_script("""
                        var el = arguments[0];
                        var orig = el.style.cssText;
                        el.style.outline = '3px solid #ff4444';
                        el.style.outlineOffset = '2px';
                        setTimeout(function(){ el.style.cssText = orig; }, 3000);
                    """, el)
                except Exception:
                    pass
                el.click()
                msg = f"已点击 {selector}"
            else:
                return ToolResult(success=False, error="需要提供 selector 或 coordinate")

            if wait_after:
                time.sleep(1)

            # 自动返回点击后的页面状态（用于验证）
            try:
                new_url = self.driver.current_url
                new_title = self.driver.title
                return ToolResult(success=True, output={
                    "message": msg,
                    "current_url": new_url,
                    "current_title": new_title,
                })
            except Exception:
                return ToolResult(success=True, output=msg)
        except Exception as e:
            return ToolResult(success=False, error=f"点击失败: {e}")

    def type(self, selector: str, text: str, clear_first: bool = True, submit: bool = False) -> ToolResult:
        """在输入框中输入文字"""
        try:
            el = self._find_element(selector)
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.2)
            try:
                self.driver.execute_script("""
                    var el = arguments[0];
                    var orig = el.style.cssText;
                    el.style.outline = '3px solid #44aaff';
                    el.style.outlineOffset = '2px';
                    setTimeout(function(){ el.style.cssText = orig; }, 3000);
                """, el)
            except Exception:
                pass

            if clear_first:
                el.clear()
            el.send_keys(text)

            if submit:
                from selenium.webdriver.common.keys import Keys
                el.send_keys(Keys.RETURN)
                time.sleep(1)

            return ToolResult(
                success=True,
                output=f"已在 {selector} 输入文字（长度: {len(text)}）"
            )
        except Exception as e:
            return ToolResult(success=False, error=f"输入失败: {e}")

    def scroll(self, direction: str = "down", amount: int = 500, selector: str = None) -> ToolResult:
        """滚动页面"""
        try:
            if selector:
                el = self.driver.find_element("css selector", selector)
                if direction == "down":
                    self.driver.execute_script(f"arguments[0].scrollTop += {amount};", el)
                elif direction == "up":
                    self.driver.execute_script(f"arguments[0].scrollTop -= {amount};", el)
                elif direction == "left":
                    self.driver.execute_script(f"arguments[0].scrollLeft -= {amount};", el)
                else:
                    self.driver.execute_script(f"arguments[0].scrollLeft += {amount};", el)
            else:
                js_directions = {"down": 0, "up": 1, "left": 2, "right": 3}
                self.driver.execute_script(f"""
                    var d = '{direction}';
                    if(d==='down') window.scrollBy(0,{amount});
                    else if(d==='up') window.scrollBy(0,-{amount});
                    else if(d==='left') window.scrollBy(-{amount},0);
                    else window.scrollBy({amount},0);
                """)

            time.sleep(0.5)
            scroll_y = self.driver.execute_script("return window.pageYOffset;")
            return ToolResult(
                success=True,
                output={"direction": direction, "amount": amount, "scroll_y": scroll_y}
            )
        except Exception as e:
            return ToolResult(success=False, error=f"滚动失败: {e}")

    def hover(self, selector: str) -> ToolResult:
        """悬停元素"""
        try:
            from selenium.webdriver.common.action_chains import ActionChains

            el = self._find_element(selector)
            try:
                self.driver.execute_script("""
                    var el = arguments[0];
                    var orig = el.style.cssText;
                    el.style.outline = '3px solid #ffaa00';
                    el.style.outlineOffset = '2px';
                    setTimeout(function(){ el.style.cssText = orig; }, 3000);
                """, el)
            except Exception:
                pass
            actions = ActionChains(self.driver)
            actions.move_to_element(el).perform()
            time.sleep(0.8)

            return ToolResult(
                success=True,
                output=f"已悬停在 {selector}"
            )
        except Exception as e:
            return ToolResult(success=False, error=f"悬停失败: {e}")

    def select_dropdown(self, selector: str, value: str, by_text: bool = False) -> ToolResult:
        """选择下拉选项"""
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import Select

            el = self.driver.find_element(By.CSS_SELECTOR, selector)
            select = Select(el)

            if by_text:
                select.select_by_visible_text(value)
            else:
                select.select_by_value(value)

            time.sleep(0.5)
            return ToolResult(
                success=True,
                output=f"已选择: {value}"
            )
        except Exception as e:
            return ToolResult(success=False, error=f"选择下拉框失败: {e}")

    def evaluate(self, script: str) -> ToolResult:
        """执行 JavaScript"""
        try:
            result = self.driver.execute_script(f"return ({script})")
            return ToolResult(
                success=True,
                output=result
            )
        except Exception as e:
            return ToolResult(success=False, error=f"JS 执行失败: {e}")

    # ─── 高级工具: scan_page ─────────────────────────────────────

    def scan_page(self) -> ToolResult:
        """
        一键扫描当前页面的全部交互项。

        等效于同时执行 get_buttons + get_links + get_forms + get_inputs +
        tab/menu 检测 + 表格检测 + 页面类型推断。
        返回结构化的 PageInteractionMap JSON。
        """
        try:
            from UI_Exploration.interaction_map import PageInteractionMap, InteractionItem

            # ── 空白页检测 + 自动刷新 ──
            body_text_len = self.driver.execute_script(
                "return document.body ? (document.body.innerText || '').trim().length : 0;"
            ) or 0
            if body_text_len < 20:
                logger.info(f"[ScanPage] 页面内容过少 ({body_text_len} 字符)，自动刷新...")
                self.driver.refresh()
                time.sleep(PAGE_LOAD_WAIT + 1)

            page_url = self.driver.current_url
            page_title = self.driver.title or ""

            page_map = PageInteractionMap(
                page_url=page_url,
                page_title=page_title,
            )

            # ── 收集按钮 ──
            buttons_result = self.get_buttons()
            if buttons_result.success:
                buttons = buttons_result.output.get("buttons", []) if isinstance(buttons_result.output, dict) else []
                for btn in buttons:
                    text = btn.get("text", "").strip()
                    if not text or self._should_skip_text(text):
                        continue
                    page_map.interactions.append(InteractionItem(
                        name=text,
                        element_type="button",
                        selector=btn.get("selector", ""),
                        page_url=page_url,
                        is_dangerous=self._is_dangerous_text(text),
                    ))

            # ── 收集链接 ──
            links_result = self.get_links()
            if links_result.success:
                links = links_result.output.get("links", []) if isinstance(links_result.output, dict) else []
                seen_hrefs = set()
                for link in links:
                    href = link.get("href", "")
                    text = link.get("text", "").strip()
                    if not text or not href or href in seen_hrefs or self._should_skip_text(text):
                        continue
                    seen_hrefs.add(href)
                    is_nav = self._is_same_origin(page_url, href) and href != page_url
                    page_map.interactions.append(InteractionItem(
                        name=text,
                        element_type="link",
                        selector=f'//a[contains(text(), "{text}")]' if text else "",
                        page_url=page_url,
                        is_navigation=is_nav,
                        is_dangerous=self._is_dangerous_text(text),
                    ))

            # ── 收集 tab/menu ──
            tab_items = self._collect_tabs_and_menus(page_url)
            page_map.interactions.extend(tab_items)

            # ── 收集下拉选择框 ──
            dropdown_items = self._collect_dropdowns(page_url)
            page_map.interactions.extend(dropdown_items)

            # ── 收集折叠面板 / 手风琴 ──
            collapsible_items = self._collect_collapsibles(page_url)
            page_map.interactions.extend(collapsible_items)

            # ── 收集可点击卡片 / 列表项 ──
            existing_selectors = {item.selector for item in page_map.interactions}
            clickable_items = self._collect_clickable_elements(page_url, existing_selectors)
            page_map.interactions.extend(clickable_items)

            # ── 收集表单 ──
            forms_result = self.get_forms()
            if forms_result.success:
                page_map.forms = forms_result.output.get("forms", []) if isinstance(forms_result.output, dict) else []

            # ── 收集表格 ──
            page_map.tables = self._collect_tables()

            # ── 推断页面类型 ──
            page_map.page_type = self._infer_page_type(page_map)

            # ── 限制数量 ──
            if len(page_map.interactions) > MAX_INTERACTIONS_PER_PAGE:
                page_map.interactions = page_map.interactions[:MAX_INTERACTIONS_PER_PAGE]

            # ── 生成摘要 ──
            page_map.summary = (
                f"页面「{page_title}」({page_map.page_type})，"
                f"包含 {page_map.interaction_count} 个交互项，"
                f"{len(page_map.forms)} 个表单，"
                f"{len(page_map.tables)} 个表格。"
            )

            # 返回给 LLM 的文本摘要（避免 JSON 过大）
            type_icons = {
                "button": "🔘", "link": "🔗", "tab": "📑", "menu_item": "📎",
                "dropdown": "📋", "collapsible": "📂", "tree_node": "🌳",
                "clickable_card": "🃏",
            }
            interaction_summary = []
            for idx, item in enumerate(page_map.interactions):
                icon = type_icons.get(item.element_type, "▪️")
                danger_mark = " ⚠️危险" if item.is_dangerous else ""
                nav_mark = " →导航" if item.is_navigation else ""
                interaction_summary.append(
                    f"  [{idx}] {icon} {item.element_type}: \"{item.name}\" (selector: {item.selector}){danger_mark}{nav_mark}"
                )

            form_summary = []
            for i, f in enumerate(page_map.forms):
                fields = [fld.get("name", fld.get("placeholder", "?")) for fld in f.get("fields", [])]
                form_summary.append(f"  表单{i}: {', '.join(fields[:5])}")

            table_summary = []
            for t in page_map.tables:
                cols = t.get("columns", [])
                table_summary.append(f"  {t.get('name', '表格')}: {', '.join(cols[:6])} ({t.get('row_count', 0)}行)")

            output_text = (
                f"📄 页面: {page_title} ({page_map.page_type})\n"
                f"🔗 URL: {page_url}\n"
                f"🔢 交互项: {page_map.interaction_count} 个\n"
                + ("\n".join(interaction_summary) + "\n" if interaction_summary else "")
                + (f"📝 表单 ({len(page_map.forms)}):\n" + "\n".join(form_summary) + "\n" if form_summary else "")
                + (f"📊 表格 ({len(page_map.tables)}):\n" + "\n".join(table_summary) + "\n" if table_summary else "")
            )

            return ToolResult(
                success=True,
                output={
                    "summary": output_text,
                    "page_url": page_url,
                    "page_title": page_title,
                    "page_type": page_map.page_type,
                    "interaction_count": page_map.interaction_count,
                    "form_count": len(page_map.forms),
                    "table_count": len(page_map.tables),
                    # 完整数据供采集器使用（不会全部发给 LLM，由 explorer 截断）
                    "_page_map_dict": page_map.to_dict(),
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=f"扫描页面失败: {e}")

    # ─── 高级工具: probe_interactions ──────────────────────────────

    def probe_interactions(self, indices: List[int] = None, max_count: int = 15) -> ToolResult:
        """
        探测当前页面交互项的效果。

        对指定的交互项依次执行「点击 → 记录效果 → 恢复状态」。
        自动跳过标记为危险的元素。

        Args:
            indices: 要探测的交互项索引列表（来自 scan_page 结果）。
                     若为空则探测所有非危险项（最多 max_count 个）。
            max_count: 最大探测数量。
        """
        try:
            from UI_Exploration.interaction_map import InteractionItem, InteractionEffect

            page_url = self.driver.current_url

            # 先执行一次 scan 拿到交互项列表
            scan_result = self.scan_page()
            if not scan_result.success:
                return ToolResult(success=False, error=f"扫描失败: {scan_result.error}")

            page_map_dict = scan_result.output.get("_page_map_dict", {})
            all_items = [InteractionItem.from_dict(d) for d in page_map_dict.get("interactions", [])]

            if not all_items:
                return ToolResult(success=True, output={
                    "summary": "页面无可探测的交互项",
                    "probed": [],
                    "new_urls": [],
                })

            # 确定要探测的项
            if indices:
                items_to_probe = [(i, all_items[i]) for i in indices if 0 <= i < len(all_items)]
            else:
                items_to_probe = [(i, item) for i, item in enumerate(all_items)
                                  if not item.is_dangerous and not self._should_skip_text(item.name)]
                items_to_probe = items_to_probe[:max_count]

            probed_results = []
            new_urls = []

            for idx, item in items_to_probe:
                effect = self._probe_single_interaction(item, page_url)
                probed_results.append({
                    "index": idx,
                    "name": item.name,
                    "type": item.element_type,
                    "effect_type": effect.effect_type,
                    "description": effect.description,
                    "target_url": effect.target_url,
                })

                if effect.effect_type == "navigate" and effect.target_url:
                    if self._is_same_origin(page_url, effect.target_url):
                        new_urls.append(effect.target_url)

                # 将效果更新到 page_map_dict
                if idx < len(page_map_dict.get("interactions", [])):
                    interaction_dict = page_map_dict["interactions"][idx]
                    interaction_dict["effects"] = [effect.to_dict()]
                    interaction_dict["probed"] = True

            # 生成摘要
            summary_lines = [f"探测了 {len(probed_results)} 个交互项:"]
            for r in probed_results:
                icon = {"navigate": "🔗", "modal": "📦", "content_change": "📝",
                        "dropdown": "📋", "error": "❌", "none": "⚪"}.get(r["effect_type"], "❓")
                summary_lines.append(
                    f"  [{r['index']}] {icon} {r['name']}: {r['description']}"
                )
            if new_urls:
                summary_lines.append(f"\n发现 {len(new_urls)} 个新页面:")
                for u in new_urls:
                    summary_lines.append(f"  🔗 {u}")

            return ToolResult(
                success=True,
                output={
                    "summary": "\n".join(summary_lines),
                    "probed": probed_results,
                    "new_urls": new_urls,
                    "probed_count": len(probed_results),
                    # 更新后的完整数据供采集器使用
                    "_page_map_dict": page_map_dict,
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=f"探测交互项失败: {e}")

    def _probe_single_interaction(self, item, current_page_url: str):
        """探测单个交互项的效果（点击 → 检测 → 恢复）"""
        from UI_Exploration.interaction_map import InteractionEffect

        try:
            # 下拉框特殊处理：点击展开 → 采集选项 → 关闭
            if item.element_type == "dropdown":
                return self._probe_dropdown(item)

            before = self._get_page_snapshot()

            # 执行点击
            click_result = self.click(item.selector, wait_after=True)
            if not click_result.success:
                return InteractionEffect(
                    effect_type="error",
                    description=f"点击失败: {click_result.error}",
                )

            time.sleep(PROBE_WAIT_SECONDS)

            after = self._get_page_snapshot()
            effect_type, description = self._detect_effect(before, after)
            target_url = after["url"] if effect_type == "navigate" else ""

            effect = InteractionEffect(
                effect_type=effect_type,
                target_url=target_url,
                description=description,
            )

            # 恢复状态
            self._restore_after_probe(before, after, current_page_url)
            return effect

        except Exception as e:
            logger.warning(f"[Probe] 探测异常: {item.name}: {e}")
            try:
                if self.driver.current_url != current_page_url:
                    self.navigate(current_page_url, timeout=10)
                    time.sleep(PAGE_LOAD_WAIT)
            except Exception:
                pass
            return InteractionEffect(effect_type="error", description=str(e))

    def _probe_dropdown(self, item):
        """探测下拉框：点击展开 → 采集可见选项 → 关闭
        
        Element UI 的 .el-select 需要点击内部的 .el-input__inner 才能展开，
        直接点击容器会报 'element not interactable'。
        """
        from UI_Exploration.interaction_map import InteractionEffect

        try:
            # 尝试多种点击策略
            click_result = None
            click_strategies = [
                item.selector,  # 原始 selector
            ]

            # 如果是 el-select / el-cascader / el-date-editor，追加内部触发器
            try:
                el = self._find_element(item.selector)
                el_classes = el.get_attribute("class") or ""
                if any(kw in el_classes for kw in ["el-select", "el-cascader", "el-date-editor", "el-time-select"]):
                    inner_sel = item.selector + " .el-input__inner"
                    click_strategies.insert(0, inner_sel)  # 优先尝试内部
                elif "ant-select" in el_classes:
                    inner_sel = item.selector + " .ant-select-selector"
                    click_strategies.insert(0, inner_sel)
            except Exception:
                pass

            for strategy_sel in click_strategies:
                click_result = self.click(strategy_sel, wait_after=True)
                if click_result.success:
                    break

            # 所有策略都失败时，尝试 JS 强制点击
            if not click_result or not click_result.success:
                try:
                    el = self._find_element(item.selector)
                    self.driver.execute_script("arguments[0].click();", el)
                    click_result = ToolResult(success=True, output="JS强制点击")
                except Exception as js_err:
                    return InteractionEffect(effect_type="error", description=f"展开下拉框失败: {js_err}")

            time.sleep(0.8)

            # 采集下拉面板中的选项
            options = self.driver.execute_script("""
                var options = [];
                // Element UI 下拉面板
                var elPanels = document.querySelectorAll('.el-select-dropdown__item, .el-cascader-menu__item, .el-dropdown-menu__item');
                elPanels.forEach(function(o) {
                    var t = o.textContent.trim();
                    if (t && t.length < 40) options.push(t);
                });
                // Ant Design 下拉面板
                document.querySelectorAll('.ant-select-item-option-content, .ant-dropdown-menu-item').forEach(function(o) {
                    var t = o.textContent.trim();
                    if (t && t.length < 40) options.push(t);
                });
                // 原生 <option>
                document.querySelectorAll('select option').forEach(function(o) {
                    var t = o.textContent.trim();
                    if (t && t.length < 40) options.push(t);
                });
                // 通用 listbox / menu
                document.querySelectorAll('[role="option"], [role="listbox"] li, .dropdown-menu li').forEach(function(o) {
                    var t = o.textContent.trim();
                    if (t && t.length < 40) options.push(t);
                });
                return [...new Set(options)].slice(0, 15);
            """) or []

            # 关闭下拉：按 Escape 或点击空白处
            try:
                from selenium.webdriver.common.keys import Keys
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.3)
            except Exception:
                pass

            desc = f"下拉框选项: {', '.join(options[:8])}" if options else "下拉框（未发现选项）"
            if len(options) > 8:
                desc += f" ...共{len(options)}项"

            return InteractionEffect(
                effect_type="dropdown",
                description=desc,
            )
        except Exception as e:
            return InteractionEffect(effect_type="error", description=f"探测下拉框失败: {e}")

    # ─── 高级工具: ensure_page_loaded ─────────────────────────────

    def ensure_page_loaded(self) -> ToolResult:
        """
        检测当前页面是否有实际内容，如果页面空白则自动刷新。

        判断逻辑：
        1. 检查 body.innerText 长度是否 < 20 字符
        2. 检查 DOM 子元素数量是否 < 3
        3. 如果判定为空白，执行 driver.refresh() 并等待加载
        4. 最多重试 2 次

        返回刷新后的页面状态。
        """
        try:
            max_retries = 2
            for attempt in range(max_retries + 1):
                page_state = self.driver.execute_script("""
                    var body = document.body;
                    if (!body) return {textLen: 0, childCount: 0, title: document.title || ''};
                    var text = (body.innerText || '').trim();
                    return {
                        textLen: text.length,
                        childCount: body.children.length,
                        title: document.title || '',
                        url: window.location.href
                    };
                """)

                text_len = page_state.get("textLen", 0)
                child_count = page_state.get("childCount", 0)
                is_empty = text_len < 20 and child_count < 5

                if not is_empty:
                    return ToolResult(
                        success=True,
                        output={
                            "status": "loaded",
                            "message": f"页面已加载，内容长度: {text_len} 字符",
                            "url": page_state.get("url", ""),
                            "title": page_state.get("title", ""),
                            "text_length": text_len,
                            "refreshed": attempt > 0,
                        }
                    )

                if attempt < max_retries:
                    logger.info(f"[EnsureLoaded] 页面空白 (text={text_len}, children={child_count})，第 {attempt + 1} 次刷新...")
                    self.driver.refresh()
                    time.sleep(PAGE_LOAD_WAIT + 1)  # 刷新后多等1秒

            # 重试完毕仍为空
            return ToolResult(
                success=True,
                output={
                    "status": "empty",
                    "message": f"页面经过 {max_retries} 次刷新仍无内容，可能是SPA路由问题或需要返回上级页面重新进入",
                    "url": self.driver.current_url,
                    "title": self.driver.title or "",
                    "text_length": text_len,
                    "refreshed": True,
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=f"页面加载检测失败: {e}")

    # ─── 高级工具: auto_login ─────────────────────────────────────

    def auto_login(self, username: str, password: str, login_url: str = None) -> ToolResult:
        """
        程序化自动登录。

        自动查找用户名/密码输入框和登录按钮，完成登录操作。
        比手动 type + click 多步操作效率高且更可靠。

        Args:
            username: 登录账号
            password: 登录密码
            login_url: 登录页 URL（可选，不提供则在当前页登录）
        """
        try:
            if login_url:
                nav_result = self.navigate(login_url, timeout=20)
                if not nav_result.success:
                    return ToolResult(success=False, error=f"无法导航到登录页: {nav_result.error}")
                time.sleep(PAGE_LOAD_WAIT)

            # 查找输入框
            inputs_result = self.get_inputs()
            if not inputs_result.success:
                return ToolResult(success=False, error="获取输入框失败")

            inputs = inputs_result.output.get("inputs", []) if isinstance(inputs_result.output, dict) else []

            username_sel = None
            password_sel = None

            for inp in inputs:
                inp_type = inp.get("type", "").lower()
                placeholder = (inp.get("placeholder", "") or "").lower()
                name = (inp.get("name", "") or "").lower()
                selector = inp.get("selector", "")

                if inp_type == "password" or "密码" in placeholder or "password" in name:
                    password_sel = selector
                elif inp_type in ("text", "tel", "email", "") or "账号" in placeholder or "用户" in placeholder or "username" in name or "account" in name:
                    if not username_sel:
                        username_sel = selector

            # 回退策略
            if not username_sel and inputs:
                for inp in inputs:
                    if inp.get("type", "").lower() != "password":
                        username_sel = inp.get("selector", "")
                        break
            if not password_sel and inputs:
                for inp in inputs:
                    if inp.get("type", "").lower() == "password":
                        password_sel = inp.get("selector", "")
                        break

            if not username_sel:
                return ToolResult(success=False, error="未找到用户名输入框")
            if not password_sel:
                return ToolResult(success=False, error="未找到密码输入框")

            # 输入凭据
            self.type(username_sel, username, clear_first=True)
            self.type(password_sel, password, clear_first=True)

            time.sleep(0.5)

            # 查找登录按钮
            buttons_result = self.get_buttons()
            login_btn_sel = None
            if buttons_result.success:
                buttons = buttons_result.output.get("buttons", []) if isinstance(buttons_result.output, dict) else []
                for btn in buttons:
                    text = (btn.get("text", "") or "").lower()
                    classes = " ".join(btn.get("classes", []))
                    if any(kw in text for kw in ["登录", "login", "sign in", "登 录", "log in"]):
                        login_btn_sel = btn.get("selector", "")
                        break
                    if "login" in classes.lower() or "submit" in classes.lower():
                        login_btn_sel = btn.get("selector", "")

            if not login_btn_sel:
                return ToolResult(success=False, error="未找到登录按钮")

            # 执行点击
            click_result = self.click(login_btn_sel, wait_after=True)
            time.sleep(PAGE_LOAD_WAIT)

            # 验证登录结果
            after_url = self.driver.current_url
            after_title = self.driver.title

            return ToolResult(
                success=True,
                output={
                    "message": "登录操作已完成",
                    "current_url": after_url,
                    "current_title": after_title,
                    "username_field": username_sel,
                    "password_field": password_sel,
                    "login_button": login_btn_sel,
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=f"自动登录失败: {e}")

    # ─── 高级工具内部辅助 ─────────────────────────────────────────

    def _get_page_snapshot(self) -> Dict:
        """获取当前页面状态快照（用于效果检测）"""
        try:
            url = self.driver.current_url
            title = self.driver.title
            body_text = self.driver.execute_script(
                "return document.body ? document.body.innerText.substring(0, 5000) : '';"
            ) or ""
            content_hash = hashlib.md5(body_text.encode("utf-8")).hexdigest()[:16]
            modal_visible = self.driver.execute_script("""
                return !!(document.querySelector('.el-dialog__wrapper:not([style*="display: none"])') ||
                          document.querySelector('.modal.show') ||
                          document.querySelector('[role="dialog"][aria-hidden="false"]') ||
                          document.querySelector('.el-message-box__wrapper:not([style*="display: none"])') ||
                          document.querySelector('.v-dialog--active') ||
                          document.querySelector('.ant-modal:not(.ant-modal-hidden)'));
            """)
            return {"url": url, "title": title, "content_hash": content_hash, "modal_visible": bool(modal_visible)}
        except Exception:
            return {"url": "", "title": "", "content_hash": "", "modal_visible": False}

    @staticmethod
    def _detect_effect(before: Dict, after: Dict) -> Tuple[str, str]:
        """对比操作前后快照判断效果"""
        if before["url"] != after["url"]:
            return "navigate", f"页面从 {before['url']} 跳转到 {after['url']}"
        if not before["modal_visible"] and after["modal_visible"]:
            return "modal", "触发了弹窗/对话框"
        if before["content_hash"] != after["content_hash"]:
            return "content_change", "页面内容发生变化"
        return "none", "无明显变化"

    def _restore_after_probe(self, before: Dict, after: Dict, fallback_url: str):
        """探测后恢复到操作前状态"""
        try:
            if before["url"] != after["url"]:
                self.navigate(fallback_url, timeout=15)
                time.sleep(PAGE_LOAD_WAIT)
                return
            if after["modal_visible"] and not before["modal_visible"]:
                if not self._try_close_modal():
                    self.navigate(fallback_url, timeout=15)
                    time.sleep(PAGE_LOAD_WAIT)
        except Exception:
            try:
                self.navigate(fallback_url, timeout=10)
                time.sleep(PAGE_LOAD_WAIT)
            except Exception:
                pass

    def _try_close_modal(self) -> bool:
        """尝试关闭模态框"""
        close_selectors = [
            ".el-dialog__headerbtn", ".el-message-box__headerbtn", ".el-drawer__close-btn",
            ".ant-modal-close", ".modal .close", "[aria-label='Close']", "[aria-label='关闭']",
            "button.close", ".v-dialog .v-btn--icon",
        ]
        for sel in close_selectors:
            try:
                elements = self.driver.find_elements("css selector", sel)
                for el in elements:
                    if el.is_displayed():
                        el.click()
                        time.sleep(0.5)
                        return True
            except Exception:
                continue
        try:
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)
            return True
        except Exception:
            pass
        return False

    def _is_interactable(self, element) -> bool:
        """宽松的可交互性检测：元素在 DOM 中且有非零尺寸即视为可交互
        
        比 is_displayed() 宽松 — 不要求在视口内，只要求：
        1. 元素未被 display:none / visibility:hidden 隐藏
        2. 元素有非零尺寸（offsetWidth > 0 或 offsetHeight > 0）
        """
        try:
            return self.driver.execute_script("""
                var el = arguments[0];
                if (!el) return false;
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (el.offsetWidth <= 0 && el.offsetHeight <= 0) {
                    var rect = el.getBoundingClientRect();
                    if (rect.width <= 0 && rect.height <= 0) return false;
                }
                return true;
            """, element)
        except Exception:
            try:
                return element.is_displayed()
            except Exception:
                return False

    def _collect_dropdowns(self, page_url: str) -> list:
        """收集下拉选择框（原生 select + UI 组件库下拉框）"""
        from UI_Exploration.interaction_map import InteractionItem

        items = []
        seen_texts = set()

        # ── 原生 <select> ──
        try:
            for sel_el in self.driver.find_elements("css selector", "select"):
                if not self._is_interactable(sel_el):
                    continue
                name = (
                    sel_el.get_attribute("aria-label")
                    or sel_el.get_attribute("name")
                    or sel_el.get_attribute("id")
                    or "下拉选择"
                )
                if name in seen_texts:
                    continue
                seen_texts.add(name)
                # 采集选项
                options = []
                for opt in sel_el.find_elements("tag name", "option"):
                    opt_text = opt.text.strip()
                    if opt_text:
                        options.append(opt_text)
                selector = self._get_selector(sel_el)
                item = InteractionItem(
                    name=f"{name} (选项: {', '.join(options[:5])}{'...' if len(options) > 5 else ''})" if options else name,
                    element_type="dropdown",
                    selector=selector,
                    page_url=page_url,
                )
                items.append(item)
        except Exception:
            pass

        # ── Element UI / Ant Design / 自定义下拉 ──
        component_selectors = [
            (".el-select", ".el-input__inner"),
            (".el-cascader", ".el-input__inner"),
            (".ant-select", ".ant-select-selection-item, .ant-select-selection-placeholder"),
            ("[role='combobox']", None),
            (".el-date-editor", ".el-input__inner"),
            (".el-time-select", ".el-input__inner"),
        ]
        for container_sel, label_sel in component_selectors:
            try:
                for container in self.driver.find_elements("css selector", container_sel):
                    if not self._is_interactable(container):
                        continue
                    # 取标签文本
                    label_text = ""
                    if label_sel:
                        try:
                            label_el = container.find_element("css selector", label_sel)
                            label_text = (
                                label_el.get_attribute("placeholder")
                                or label_el.text.strip()
                                or label_el.get_attribute("value")
                                or ""
                            )
                        except Exception:
                            pass
                    if not label_text:
                        label_text = (
                            container.get_attribute("aria-label")
                            or container.get_attribute("placeholder")
                            or container.text.strip()[:30]
                            or "下拉选择框"
                        )
                    if label_text in seen_texts or self._should_skip_text(label_text):
                        continue
                    seen_texts.add(label_text)
                    selector = self._get_selector(container)
                    items.append(InteractionItem(
                        name=label_text,
                        element_type="dropdown",
                        selector=selector,
                        page_url=page_url,
                    ))
            except Exception:
                continue

        return items

    def _collect_collapsibles(self, page_url: str) -> list:
        """收集折叠面板 / 手风琴 / 展开收缩区域"""
        from UI_Exploration.interaction_map import InteractionItem

        items = []
        seen_texts = set()

        collapsible_selectors = [
            # HTML5 <details><summary>
            ("details > summary", "collapsible"),
            # Element UI
            (".el-collapse-item__header", "collapsible"),
            # Ant Design
            (".ant-collapse-header", "collapsible"),
            # Bootstrap
            ("[data-bs-toggle='collapse']", "collapsible"),
            ("[data-toggle='collapse']", "collapsible"),
            # 通用 ARIA
            ("[aria-expanded]", "collapsible"),
            # 树节点
            (".el-tree-node__content", "tree_node"),
            (".ant-tree-treenode", "tree_node"),
        ]

        for css_sel, elem_type in collapsible_selectors:
            try:
                elements = self.driver.find_elements("css selector", css_sel)
                for el in elements:
                    if not self._is_interactable(el):
                        continue
                    text = el.text.strip()
                    if not text or len(text) > 50 or text in seen_texts or self._should_skip_text(text):
                        continue
                    seen_texts.add(text)
                    selector = self._get_selector(el)
                    items.append(InteractionItem(
                        name=text,
                        element_type=elem_type,
                        selector=selector,
                        page_url=page_url,
                    ))
            except Exception:
                continue

        return items

    def _collect_clickable_elements(self, page_url: str, existing_selectors: set) -> list:
        """收集带有点击行为的卡片、列表项等非标准可交互元素
        
        通过 JS 检测 cursor:pointer + 有文本 + 非已收录元素的 div/li/span/a 等。
        """
        from UI_Exploration.interaction_map import InteractionItem

        items = []
        try:
            clickable_data = self.driver.execute_script("""
                var results = [];
                var seen = new Set();
                // 优先扫描常见的卡片/列表容器
                var candidates = document.querySelectorAll(
                    '.card, .el-card, .ant-card, [class*="card"], ' +
                    '.list-item, .el-list-item, [class*="list-item"], ' +
                    'li[onclick], div[onclick], span[onclick], ' +
                    '[class*="clickable"], [class*="pointer"]'
                );
                for (var i = 0; i < candidates.length && results.length < 20; i++) {
                    var el = candidates[i];
                    var style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (el.offsetWidth <= 0 && el.offsetHeight <= 0) continue;
                    var text = (el.textContent || '').trim().substring(0, 60);
                    if (!text || text.length < 2) continue;
                    // 检查是否有 cursor:pointer 或 onclick 属性
                    var hasPointer = style.cursor === 'pointer';
                    var hasOnclick = el.hasAttribute('onclick') || el.hasAttribute('v-on:click');
                    if (!hasPointer && !hasOnclick) continue;
                    // 跳过已经是 button/a/input 的元素（已被其他收集器处理）
                    var tag = el.tagName.toLowerCase();
                    if (tag === 'button' || tag === 'a' || tag === 'input' || tag === 'select') continue;
                    // 生成唯一标识避免重复
                    var sig = tag + ':' + text.substring(0, 30);
                    if (seen.has(sig)) continue;
                    seen.add(sig);
                    // 构建 selector
                    var selector = '';
                    if (el.id) {
                        selector = '#' + el.id;
                    } else {
                        var cls = Array.from(el.classList).filter(function(c) {
                            return c.length > 2 && c.length < 30;
                        }).slice(0, 2).join('.');
                        selector = cls ? (tag + '.' + cls) : '';
                    }
                    if (!selector) continue;
                    results.push({
                        text: text,
                        selector: selector,
                        tag: tag
                    });
                }
                return results;
            """)

            for item_data in (clickable_data or []):
                text = item_data.get("text", "")
                selector = item_data.get("selector", "")
                if selector in existing_selectors or self._should_skip_text(text):
                    continue
                items.append(InteractionItem(
                    name=text[:40],
                    element_type="clickable_card",
                    selector=selector,
                    page_url=page_url,
                    is_dangerous=self._is_dangerous_text(text),
                ))
        except Exception as e:
            logger.debug(f"[ScanPage] 可点击元素收集异常: {e}")

        return items

    def _collect_tabs_and_menus(self, page_url: str) -> list:
        """收集页面中的标签切换和导航菜单项"""
        from UI_Exploration.interaction_map import InteractionItem

        items = []
        selectors = [
            ("div.tab-item", "tab"), (".el-tabs__item", "tab"), (".el-menu-item", "menu_item"),
            (".ant-tabs-tab", "tab"), (".nav-link", "menu_item"), (".sidebar-menu-item", "menu_item"),
            ("nav a", "menu_item"), ("[role='tab']", "tab"), ("[role='menuitem']", "menu_item"),
        ]
        seen_texts = set()
        for css_sel, elem_type in selectors:
            try:
                elements = self.driver.find_elements("css selector", css_sel)
                for el in elements:
                    try:
                        text = el.text.strip()
                        if not text or text in seen_texts or self._should_skip_text(text) or len(text) > 30:
                            continue
                        if not el.is_displayed():
                            continue
                        seen_texts.add(text)
                        cls_part = css_sel.split(".")[-1] if "." in css_sel else ""
                        selector = f'//{el.tag_name}[contains(@class, "{cls_part}") and contains(text(), "{text}")]'
                        items.append(InteractionItem(
                            name=text, element_type=elem_type, selector=selector,
                            page_url=page_url, is_dangerous=self._is_dangerous_text(text),
                        ))
                    except Exception:
                        continue
            except Exception:
                continue
        return items

    def _collect_tables(self) -> list:
        """收集页面表格信息"""
        try:
            return self.driver.execute_script("""
                var tables = [];
                document.querySelectorAll('table').forEach(function(t, i) {
                    var headers = [];
                    t.querySelectorAll('thead th, thead td').forEach(function(th) {
                        var text = th.textContent.trim();
                        if (text) headers.push(text);
                    });
                    tables.push({
                        name: t.getAttribute('aria-label') || ('表格' + (i+1)),
                        columns: headers,
                        row_count: t.querySelectorAll('tbody tr').length,
                        has_pagination: !!document.querySelector('.el-pagination, .ant-pagination, [class*="pagination"]'),
                    });
                });
                document.querySelectorAll('.el-table, .ant-table').forEach(function(t, i) {
                    if (tables.length > 0) return;
                    var headers = [];
                    t.querySelectorAll('.el-table__header th, .ant-table-thead th').forEach(function(th) {
                        var text = th.textContent.trim();
                        if (text) headers.push(text);
                    });
                    tables.push({
                        name: '数据表格' + (i+1),
                        columns: headers,
                        row_count: t.querySelectorAll('.el-table__body tr, .ant-table-tbody tr').length,
                        has_pagination: !!document.querySelector('.el-pagination, .ant-pagination'),
                    });
                });
                return tables;
            """) or []
        except Exception:
            return []

    @staticmethod
    def _infer_page_type(page_map) -> str:
        """根据页面元素推断页面类型"""
        title = page_map.page_title.lower()
        has_form = len(page_map.forms) > 0
        has_table = len(page_map.tables) > 0

        if any(kw in title for kw in ["登录", "login", "sign in"]):
            return "login"
        if any(kw in title for kw in ["注册", "register", "sign up"]):
            return "register"
        if any(kw in title for kw in ["仪表", "dashboard", "首页", "概览"]):
            return "dashboard"
        if has_table and not has_form:
            return "list"
        if has_form and not has_table:
            return "form"
        if has_form and has_table:
            return "mixed"
        if any(kw in title for kw in ["详情", "detail"]):
            return "detail"
        return "mixed"

    @staticmethod
    def _is_dangerous_text(text: str) -> bool:
        text_lower = text.lower().strip()
        return any(kw.lower() in text_lower for kw in DANGEROUS_KEYWORDS)

    @staticmethod
    def _should_skip_text(text: str) -> bool:
        text_lower = text.lower().strip()
        if not text_lower or len(text_lower) > 50:
            return True
        return any(kw.lower() in text_lower for kw in SKIP_KEYWORDS)

    @staticmethod
    def _is_same_origin(base_url: str, target_url: str) -> bool:
        try:
            base = urlparse(base_url)
            target = urlparse(target_url)
            return base.scheme == target.scheme and base.netloc == target.netloc
        except Exception:
            return False

    # ─── 工具分发 ──────────────────────────────────────────────────

    def execute(self, tool_name: str, params: dict) -> ToolResult:
        """
        分发工具执行请求

        Args:
            tool_name: 工具名称（对应 TOOL_DEFINITIONS 中的 name）
            params: 工具参数字典
        """
        dispatcher = {
            "navigate": lambda p: self.navigate(p.get("url", ""), p.get("timeout", 30)),
            "screenshot": lambda p: self.screenshot(p.get("full_page", False), p.get("filename")),
            "read_page": lambda p: self.read_page(p.get("dom", True), p.get("max_length", 15000)),
            "find": lambda p: self.find(p.get("query", ""), p.get("by", "css")),
            "click": lambda p: self.click(p.get("selector"), p.get("coordinate"), p.get("wait_after", True)),
            "type": lambda p: self.type(p.get("selector", ""), p.get("text", ""),
                                          p.get("clear_first", True), p.get("submit", False)),
            "scroll": lambda p: self.scroll(p.get("direction", "down"), p.get("amount", 500),
                                           p.get("selector")),
            "hover": lambda p: self.hover(p.get("selector", "")),
            "evaluate": lambda p: self.evaluate(p.get("script", "")),
            "get_links": lambda p: self.get_links(p.get("filter_external", False)),
            "get_forms": lambda p: self.get_forms(),
            "get_buttons": lambda p: self.get_buttons(),
            "get_inputs": lambda p: self.get_inputs(),
            "select_dropdown": lambda p: self.select_dropdown(p.get("selector", ""),
                                                               p.get("value", ""), p.get("by_text", False)),
            "wait": lambda p: self.wait(p.get("seconds", 2), p.get("selector")),
            # 高级工具
            "scan_page": lambda p: self.scan_page(),
            "probe_interactions": lambda p: self.probe_interactions(
                indices=p.get("indices"), max_count=p.get("max_count", 15)),
            "auto_login": lambda p: self.auto_login(
                p.get("username", ""), p.get("password", ""), p.get("login_url")),
            "ensure_page_loaded": lambda p: self.ensure_page_loaded(),
        }

        handler = dispatcher.get(tool_name)
        if not handler:
            return ToolResult(success=False, error=f"未知工具: {tool_name}")
        return handler(params)

    # ─── 内部辅助方法 ──────────────────────────────────────────────

    @staticmethod
    def _element_info(el) -> dict:
        """提取元素的基本信息"""
        try:
            rect = el.rect
            location = {"x": round(rect['x'], 1), "y": round(rect['y'], 1),
                        "width": round(rect['width'], 1), "height": round(rect['height'], 1)} if rect else {}
        except Exception:
            location = {}

        return {
            "tag": el.tag_name,
            "selector": BrowserTools._get_selector(el),
            "id": el.get_attribute("id") or "",
            "classes": (el.get_attribute("class") or "").split()[:5],
            "type": el.get_attribute("type") or "",
            "name": el.get_attribute("name") or "",
            "text": el.text[:100] if el.text else "",
            "placeholder": el.get_attribute("placeholder") or "",
            "href": el.get_attribute("href") or "",
            "visible": el.is_displayed(),
            "enabled": el.is_enabled(),
            "rect": location,
        }

    @staticmethod
    def _get_selector(el) -> str:
        """生成元素的 CSS 选择器"""
        try:
            elem_id = el.get_attribute("id")
            if elem_id and elem_id.isidentifier():
                return f"#{elem_id}"

            # 尝试通过 class 生成选择器
            classes = (el.get_attribute("class") or "").strip().split()
            if classes and len(classes) <= 3 and all(c.isidentifier() for c in classes):
                class_sel = ".".join(classes[:2])
                return f"{el.tag_name}.{class_sel}"

            # 回退：用 nth-of-type
            parent = el.find_element("xpath", "..")
            siblings = parent.find_elements("xpath", f"./{el.tag_name}")
            idx = siblings.index(el) + 1
            return f"{el.tag_name}:nth-of-type({idx})"
        except Exception:
            return el.tag_name or "*"
