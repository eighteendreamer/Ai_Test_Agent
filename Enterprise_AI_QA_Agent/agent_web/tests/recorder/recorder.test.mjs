// @vitest-environment jsdom
//
// recorder.js 注入脚本单测（P0-4 验收：locator 优先级、脱敏、节流合并、dom_hash 稳定性）。
// 脚本由后端持有（Agent_Server/src/application/recorder/assets/recorder.js，方案第 8 章），
// 测试放在 agent_web——仓库内唯一的 JS 测试基建（vitest + jsdom）。
//
// 隔离策略：每个用例创建独立 JSDOM 实例并在其 realm 内 eval 脚本（等价真实
// 浏览器「整页导航 = 全新 document」语义，避免共享窗口下监听器叠加）；
// window 计时器与 Date 重定向到 vitest 假定时器，debounce/节流可同步推进。

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// jsdom 环境下 import.meta.url 不是 file 协议，改用 cwd 相对定位后端资产
const RECORDER_PATH = resolve(
  process.cwd(),
  "../Agent_Server/src/application/recorder/assets/recorder.js",
);
const RECORDER_CODE = readFileSync(RECORDER_PATH, "utf8");

const realSetTimeout = globalThis.setTimeout;

/** @type {Array<any>} */
const events = [];

function createWindow({ storage = {} } = {}) {
  const dom = new JSDOM(
    `<!doctype html><html><head><title>测试页</title></head><body></body></html>`,
    // runScripts: "outside-only" —— 允许 window.eval 执行注入脚本（默认关闭时
    // eval 静默不生效，控制协议不会挂载），但不自动执行页面内 <script>
    { url: "http://localhost/", pretendToBeVisual: true, runScripts: "outside-only" },
  );
  const win = dom.window;
  win.setTimeout = globalThis.setTimeout;
  win.clearTimeout = globalThis.clearTimeout;
  win.setInterval = globalThis.setInterval;
  win.clearInterval = globalThis.clearInterval;
  win.Date = globalThis.Date;
  for (const [key, value] of Object.entries(storage)) {
    win.sessionStorage.setItem(key, value);
  }
  win.__qaRecordEmit = (s) => events.push(JSON.parse(s));
  win.eval(RECORDER_CODE);
  return { dom, win, doc: win.document };
}

function enabledWindow(options) {
  const ctx = createWindow(options);
  ctx.win.__qaRecorderSetEnabled(true);
  vi.advanceTimersByTime(600); // 冲掉启用时的首次 page_scan
  events.length = 0;
  return ctx;
}

function click(win, el, x = 10, y = 20) {
  el.dispatchEvent(
    new win.MouseEvent("click", { bubbles: true, composed: true, cancelable: true, clientX: x, clientY: y }),
  );
}

function input(win, el, value) {
  el.value = value;
  el.dispatchEvent(new win.Event("input", { bubbles: true, composed: true }));
}

beforeEach(() => {
  events.length = 0;
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("recorder.js 安装与控制", () => {
  it("幂等守卫：同窗口重复注入不重复初始化", () => {
    const { win, doc } = enabledWindow();
    click(win, doc.body);
    expect(events.length).toBe(1);
    win.eval(RECORDER_CODE); // 第二次注入被 __qaRecorderInstalled 挡住
    click(win, doc.body);
    expect(events.length).toBe(2);
  });

  it("采集开关：关闭期间不采集，重新开启恢复", () => {
    const { win, doc } = enabledWindow();
    const btn = doc.createElement("button");
    btn.textContent = "按钮";
    doc.body.appendChild(btn);

    win.__qaRecorderSetEnabled(false);
    click(win, btn);
    expect(events.length).toBe(0);

    win.__qaRecorderSetEnabled(true);
    vi.advanceTimersByTime(600);
    events.length = 0;
    click(win, btn);
    expect(events.length).toBe(1);
  });

  it("事件缓冲：binding 缺失时入队，binding 就绪后 flush 补投", () => {
    const { win, doc } = enabledWindow();
    delete win.__qaRecordEmit;
    click(win, doc.body);
    expect(win.__qaRecorderGetState().pending).toBe(1);
    expect(events.length).toBe(0);

    win.__qaRecordEmit = (s) => events.push(JSON.parse(s));
    win.__qaRecorderFlush();
    expect(events.length).toBe(1);
  });
});

describe("点击与双重定位", () => {
  it("locator 链完整（id/testid/role+name/css/xpath/text）与像素三件套", () => {
    const { win, doc } = enabledWindow();
    const btn = doc.createElement("button");
    btn.id = "login-submit";
    btn.setAttribute("data-testid", "submit-btn");
    btn.setAttribute("type", "submit");
    btn.textContent = "登 录";
    doc.body.appendChild(btn);

    click(win, btn, 712, 503);
    expect(events.length).toBe(1);
    const ev = events[0];
    expect(ev.type).toBe("click");
    expect(Number.isInteger(ev.seq)).toBe(true); // 绝对起点由“seq 连续性”组精确验证
    expect(ev.timestamp).toBeTruthy();
    expect(ev.page.url).toBe("http://localhost/");
    expect(ev.page.title).toBe("测试页");
    expect(ev.page.viewport).toEqual({ w: expect.any(Number), h: expect.any(Number) });
    expect(ev.page.dpr).toBeGreaterThanOrEqual(1);

    expect(ev.target.locators.id).toBe("login-submit");
    expect(ev.target.locators.testid).toBe("submit-btn");
    expect(ev.target.locators.role_name).toEqual({ role: "button", name: "登 录" });
    expect(ev.target.locators.css).toBe("#login-submit");
    expect(ev.target.locators.xpath).toBe("/html/body[1]/button[1]");
    expect(ev.target.locators.text).toBe("登 录");
    expect(ev.target.tag).toBe("BUTTON");
    expect(ev.target.attributes.type).toBe("submit");
    expect(ev.target.attributes["data-testid"]).toBe("submit-btn");

    expect(ev.pixel.viewport_point).toEqual({ x: 712, y: 503 });
    expect(ev.pixel.bbox).toEqual({ x: expect.any(Number), y: expect.any(Number), w: expect.any(Number), h: expect.any(Number) });
    expect(ev.pixel.rel_offset).toHaveProperty("rx");
    expect(ev.pixel.rel_offset).toHaveProperty("ry");
  });

  it("无 id 元素生成 nth-of-type css 与带序号 xpath", () => {
    const { win, doc } = enabledWindow();
    const div = doc.createElement("div");
    const first = doc.createElement("button");
    first.textContent = "一";
    const second = doc.createElement("button");
    second.textContent = "二";
    div.append(first, second);
    doc.body.appendChild(div);

    click(win, second);
    const ev = events[0];
    expect(ev.target.locators.css).toBe("html > body > div > button:nth-of-type(2)");
    expect(ev.target.locators.xpath).toBe("/html/body[1]/div[1]/button[2]");
  });

  it("dblclick 与 submit 全量采集", () => {
    const { win, doc } = enabledWindow();
    const form = doc.createElement("form");
    form.id = "f";
    doc.body.appendChild(form);
    form.dispatchEvent(new win.Event("submit", { bubbles: true, composed: true }));
    doc.body.dispatchEvent(new win.MouseEvent("dblclick", { bubbles: true, composed: true }));

    expect(events.map((e) => e.type)).toEqual(["submit", "dblclick"]);
    expect(events[0].target.tag).toBe("FORM");
  });
});

describe("输入采集", () => {
  it("input 500ms debounce 合并为一次 fill，保留最终值", () => {
    const { win, doc } = enabledWindow();
    const el = doc.createElement("input");
    el.type = "text";
    el.id = "username";
    doc.body.appendChild(el);

    for (const value of ["", "a", "ab", "abc"]) input(win, el, value);

    expect(events.length).toBe(0); // 未到 500ms 不出
    vi.advanceTimersByTime(500);

    expect(events.length).toBe(1);
    expect(events[0].type).toBe("fill");
    expect(events[0].value).toBe("abc");
    expect(events[0].target.locators.id).toBe("username");
  });

  it("密码字段只记长度不记明文（安全红线）", () => {
    const { win, doc } = enabledWindow();
    const pwd = doc.createElement("input");
    pwd.type = "password";
    pwd.id = "pwd";
    doc.body.appendChild(pwd);
    input(win, pwd, "secret123");
    vi.advanceTimersByTime(500);

    expect(events.length).toBe(1);
    expect(events[0].value).toEqual({ length: 9 });
    expect(JSON.stringify(events[0])).not.toContain("secret123");
  });

  it("命名暗示敏感的普通字段同样脱敏", () => {
    const { win, doc } = enabledWindow();
    const el = doc.createElement("input");
    el.type = "text";
    el.name = "user_pwd";
    doc.body.appendChild(el);
    input(win, el, "hunter2");
    vi.advanceTimersByTime(500);

    expect(events[0].value).toEqual({ length: 7 });
  });

  it("change 事件立即结算最终值（不等待 debounce）", () => {
    const { win, doc } = enabledWindow();
    const el = doc.createElement("input");
    el.type = "text";
    doc.body.appendChild(el);
    input(win, el, "partial");
    el.value = "final";
    el.dispatchEvent(new win.Event("change", { bubbles: true, composed: true }));

    expect(events.length).toBe(1);
    expect(events[0].value).toBe("final");
    vi.advanceTimersByTime(600);
    expect(events.length).toBe(1); // 不重复出
  });
});

describe("按键采集", () => {
  it("普通字符键不采集，功能键与组合键采集", () => {
    const { win, doc } = enabledWindow();
    const press = (key, opts = {}) =>
      doc.body.dispatchEvent(new win.KeyboardEvent("keydown", { key, bubbles: true, composed: true, ...opts }));

    press("a");
    press("Enter");
    press("s", { ctrlKey: true });
    press("ArrowDown");
    press("Shift"); // 纯修饰键忽略

    expect(events.map((e) => e.value)).toEqual(["Enter", "ctrl+s", "ArrowDown"]);
  });
});

describe("滚动采集", () => {
  it("300ms 节流：窗口期内的滚动合并，跨窗口记录最新位置", () => {
    const { win, doc } = enabledWindow();
    const box = doc.createElement("div");
    doc.body.appendChild(box);

    box.scrollTop = 100;
    box.dispatchEvent(new win.Event("scroll")); // 无 bubbles，靠捕获阶段接收
    box.scrollTop = 250;
    box.dispatchEvent(new win.Event("scroll")); // 节流窗口内 → 丢弃

    expect(events.length).toBe(1);
    expect(events[0].type).toBe("scroll");
    expect(events[0].value.scroll_top).toBe(100);

    vi.advanceTimersByTime(300);
    box.scrollTop = 400;
    box.dispatchEvent(new win.Event("scroll"));
    expect(events.length).toBe(2);
    expect(events[1].value.scroll_top).toBe(400);
    expect(typeof events[0].value.container).toBe("string");
  });
});

describe("导航采集", () => {
  it("history.pushState 产生 navigate 事件（from/to）并触发 page_scan", () => {
    const { win } = enabledWindow();
    const from = win.location.href;
    win.history.pushState(null, "", "/orders?page=1");
    expect(events.length).toBe(1);
    expect(events[0].type).toBe("navigate");
    expect(events[0].value.from).toBe(from);
    expect(events[0].value.to).toBe("http://localhost/orders?page=1");
    expect(events[0].page_effect.navigated_to).toBe("http://localhost/orders?page=1");

    vi.advanceTimersByTime(600); // 导航后触发 page_scan
    expect(events.some((e) => e.type === "page_scan")).toBe(true);
  });

  it("popstate 产生 navigate 事件", () => {
    const { win } = enabledWindow();
    win.history.pushState(null, "", "/a");
    events.length = 0;
    win.dispatchEvent(new win.Event("popstate"));
    expect(events.length).toBe(1);
    expect(events[0].type).toBe("navigate");
    expect(events[0].value.to).toBe("http://localhost/a");
  });
});

describe("seq 连续性", () => {
  it("seq 从 0 单调递增，挂起的 page_scan 按发出顺序续号", () => {
    const { win, doc } = createWindow();
    win.__qaRecorderSetEnabled(true);
    // 不推进定时器：启用时的首次 page_scan 尚挂起，用户事件先发出
    click(win, doc.body);
    click(win, doc.body);
    expect(events.map((e) => e.seq)).toEqual([0, 1]);

    vi.advanceTimersByTime(600); // 挂起的 page_scan 在用户事件之后发出 → seq 2
    expect(events[2].type).toBe("page_scan");
    expect(events[2].seq).toBe(2);
  });

  it("整页导航重注入后从 sessionStorage 续号", () => {
    const ctx1 = enabledWindow();
    click(ctx1.win, ctx1.doc.body);
    const lastSeq = events[events.length - 1].seq;

    // 模拟同 tab 整页导航：全新 document（新 JSDOM），sessionStorage 延续
    const ctx2 = createWindow({
      storage: { __qaRecorderSeq: String(lastSeq + 1), __qaRecorderLastUrl: "http://localhost/" },
    });
    ctx2.win.__qaRecorderSetEnabled(true);
    events.length = 0; // 丢弃 ctx1 的历史事件，只看新文档的续号
    click(ctx2.win, ctx2.doc.body);
    expect(events[0].seq).toBe(lastSeq + 1);
  });
});

describe("DOM 指纹与页面扫描", () => {
  it("sha1 实现与 Node crypto 一致（含中文/emoji UTF-8）", () => {
    const { win } = enabledWindow();
    for (const c of ["", "abc", "button|button|登 录|", "混合中文🚀emoji"]) {
      expect(win.__qaRecorderSha1(c)).toBe(createHash("sha1").update(c, "utf8").digest("hex"));
    }
  });

  it("同 DOM 两次扫描 dom_hash 相同；DOM 变化 hash 变化", () => {
    const { win, doc } = enabledWindow();
    const btn = doc.createElement("button");
    btn.textContent = "提交";
    doc.body.appendChild(btn);
    const a = doc.createElement("a");
    a.href = "/detail";
    a.textContent = "详情";
    doc.body.appendChild(a);

    const s1 = win.__qaRecorderScan();
    const s2 = win.__qaRecorderScan();
    expect(s1.dom_hash).toBe(s2.dom_hash);
    expect(s1.dom_hash).toMatch(/^[0-9a-f]{40}$/);
    expect(s1.interactive_count).toBe(2);
    expect(s1.interactive_elements).toEqual([
      { tag: "button", role: "button", name: "提交", href: "" },
      { tag: "a", role: "link", name: "详情", href: "/detail" },
    ]);

    const extra = doc.createElement("button");
    extra.textContent = "新增";
    doc.body.appendChild(extra);
    const s3 = win.__qaRecorderScan();
    expect(s3.dom_hash).not.toBe(s1.dom_hash);
    expect(s3.interactive_count).toBe(3);
  });

  it("page_scan 事件携带 dom_hash 与可交互元素清单", () => {
    const { win, doc } = enabledWindow();
    const btn = doc.createElement("button");
    btn.textContent = "扫描按钮";
    doc.body.appendChild(btn);

    win.dispatchEvent(new win.Event("pageshow")); // 同 URL → 触发重扫描
    vi.advanceTimersByTime(600);

    const scan = events.find((e) => e.type === "page_scan");
    expect(scan).toBeTruthy();
    expect(scan.page_effect.dom_hash).toMatch(/^[0-9a-f]{40}$/);
    expect(scan.page_effect.interactive_count).toBeGreaterThanOrEqual(1);
    expect(scan.page_effect.interactive_elements[0].name).toBe("扫描按钮");
    expect(scan.target).toBeNull();
  });
});

describe("MutationObserver 计数", () => {
  it("事件携带自上一事件以来的 DOM 变更计数，且发出后重置", async () => {
    const { win, doc } = enabledWindow();
    doc.body.appendChild(doc.createElement("span"));
    doc.body.appendChild(doc.createElement("span"));
    await new Promise((r) => realSetTimeout(r, 5)); // 等 jsdom 派发 mutation 回调

    click(win, doc.body);
    expect(events[0].page_effect.dom_mutation_count).toBeGreaterThan(0);

    click(win, doc.body);
    expect(events[1].page_effect.dom_mutation_count).toBe(0); // 计数已重置
  });
});
