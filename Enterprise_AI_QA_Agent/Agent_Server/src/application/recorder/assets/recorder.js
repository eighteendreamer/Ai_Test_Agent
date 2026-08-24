/**
 * recorder.js — UI 操作录制注入脚本（方案 6.1–6.4，三端共用，单文件无依赖）
 *
 * 注入时机：文档创建前（CDP Page.addScriptToEvaluateOnNewDocument / Playwright
 * add_init_script），SPA 路由切换后仍存活；整页导航后脚本重注入，seq 与 lastUrl
 * 通过 sessionStorage 保持连续。
 *
 * 事件回传：top frame 统一分配 seq 后调用 window.__qaRecordEmit(jsonString)
 * （embedded: CDP Runtime.addBinding；playwright: exposeBinding；宿主侧缓冲
 * 批量 POST /api/v1/recordings/{id}/events:batch）。
 *
 * 控制协议（驱动侧 Runtime.evaluate 调用）：
 *   window.__qaRecorderSetEnabled(bool)  开始 / 暂停·继续
 *   window.__qaRecorderGetState()        → {enabled, seq, lastUrl, pending}
 *   window.__qaRecorderScan()            立即触发一次页面轻量扫描（dom_hash）
 *   window.__qaRecorderFlush()           立即冲刷本地缓冲（调试用）
 *
 * P0 边界（方案 6.1 / 11）：跨域 iframe 不采集；Canvas 由像素三件套兜底。
 */
(function () {
  'use strict';

  if (window.__qaRecorderInstalled) {
    return;
  }
  window.__qaRecorderInstalled = true;

  var EMIT_BINDING = '__qaRecordEmit';
  var SEQ_KEY = '__qaRecorderSeq';
  var LAST_URL_KEY = '__qaRecorderLastUrl';
  var INPUT_DEBOUNCE_MS = 500;
  var SCROLL_THROTTLE_MS = 300;
  var SCAN_SETTLE_MS = 500;
  var MAX_TEXT_LEN = 100;
  var MAX_VALUE_LEN = 10000;
  var MAX_ATTR_LEN = 200;
  var MAX_SCAN_ELEMENTS = 200;
  var SENSITIVE_NAME_RE = /pass(word)?|pwd|secret|token|credential|otp|verif(?:y|ication)[-_]?code/i;
  var FUNCTION_KEYS = {
    Enter: 1, Tab: 1, Escape: 1, ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1,
    Home: 1, End: 1, PageUp: 1, PageDown: 1, Backspace: 1, Delete: 1, Insert: 1,
  };
  var MODIFIER_KEYS = { Shift: 1, Control: 1, Alt: 1, Meta: 1, Dead: 1 };

  var IMPLICIT_ROLES = {
    button: 'button', a: 'link', select: 'combobox', textarea: 'textbox',
    input: 'textbox', h1: 'heading', h2: 'heading', h3: 'heading', h4: 'heading',
    h5: 'heading', h6: 'heading', nav: 'navigation', main: 'main', aside: 'complementary',
    header: 'banner', footer: 'contentinfo', ul: 'list', ol: 'list', li: 'listitem',
    img: 'img', form: 'form', table: 'table', dialog: 'dialog', meter: 'meter', progress: 'progressbar',
  };
  var INPUT_TYPE_ROLES = {
    checkbox: 'checkbox', radio: 'radio', range: 'slider', number: 'spinbutton',
    search: 'searchbox', email: 'textbox', tel: 'textbox', text: 'textbox',
    url: 'textbox', password: 'textbox', submit: 'button', button: 'button',
    reset: 'button', date: 'textbox', datetime: 'textbox',
  };
  var INTERACTIVE_SELECTOR =
    'button, a, input, select, textarea, [role], [tabindex], summary, details';

  var isTop = (function () {
    try {
      return window.top === window;
    } catch (e) {
      return false; // 跨域子 frame：访问 top 抛错 → 走 postMessage 桥（P1-3）
    }
  })();

  var state = {
    enabled: false,
    seq: readNumber(SEQ_KEY, 0),
    lastUrl: safeStorage('get', LAST_URL_KEY) || null,
    mutationCount: 0,
    pending: [],
    fillTimers: new WeakMap(),   // element → {timer, snapshot}
    scrollLastAt: new WeakMap(), // element → lastEmitTs
    scanTimer: null,
  };

  // ------------------------------------------------------------ seq / emit

  function safeStorage(op, key, value) {
    try {
      if (op === 'get') return window.sessionStorage.getItem(key);
      window.sessionStorage.setItem(key, value);
    } catch (e) {
      /* 隐私模式/受限环境：seq 退化为本次文档内单调 */
    }
    return null;
  }

  function readNumber(key, fallback) {
    var raw = safeStorage('get', key);
    var n = raw === null ? NaN : Number(raw);
    return Number.isFinite(n) && n >= 0 ? Math.floor(n) : fallback;
  }

  function nextSeq() {
    var n = state.seq;
    state.seq = n + 1;
    safeStorage('set', SEQ_KEY, String(state.seq));
    return n;
  }

  function emit(rawEvent) {
    if (!isTop) {
      bridgeToTop(rawEvent); // 子 frame 不分配 seq，交由 top 统一编排
      return;
    }
    rawEvent.seq = nextSeq();
    rawEvent.timestamp = new Date().toISOString();
    rawEvent.page_effect = rawEvent.page_effect || {};
    rawEvent.page_effect.dom_mutation_count = state.mutationCount;
    state.mutationCount = 0;
    var payload = JSON.stringify(rawEvent);
    var binding = window[EMIT_BINDING];
    if (typeof binding === 'function') {
      try {
        binding(payload);
        return;
      } catch (e) {
        /* binding 异常落入缓冲，稍后重试 */
      }
    }
    state.pending.push(payload);
  }

  function flushPending() {
    if (!state.pending.length) return;
    var binding = window[EMIT_BINDING];
    if (typeof binding !== 'function') return;
    var queued = state.pending.splice(0, state.pending.length);
    for (var i = 0; i < queued.length; i++) {
      try {
        binding(queued[i]);
      } catch (e) {
        state.pending.push(queued[i]);
      }
    }
  }

  setInterval(flushPending, 2000);

  // -------------------------------------------------------- iframe 桥接

  function bridgeToTop(rawEvent) {
    try {
      if (window.parent && window.parent !== window) {
        // targetOrigin 用 '*'：top 与子 frame 跨域时，传 sender 自身 origin 会被
        // 浏览器按 targetOrigin 不匹配静默丢弃（P1-3 修复）。负载已脱敏；
        // 页面脚本本可 dispatchEvent 伪造真实事件，宽松送达不新增伪造面。
        window.parent.postMessage({ __qaRecorderBridge: 1, event: rawEvent }, '*');
      }
    } catch (e) {
      /* 桥接受限：丢弃（宁可漏过，不可误杀宿主页面） */
    }
  }

  if (isTop) {
    window.addEventListener('message', function (e) {
      // 跨域 iframe 是合法来源（同源/跨域统一经 __qaRecorderBridge 协议标记），
      // seq 由 top 统一分配，多 frame 汇聚不冲突。
      if (!e.data || e.data.__qaRecorderBridge !== 1 || !e.data.event) return;
      var raw = e.data.event;
      if (raw.target) raw.target.in_iframe = true;
      emit(raw);
    }, true);
  }

  // ------------------------------------------------------- 目标解析

  function eventElement(e) {
    var path = null;
    try {
      path = e.composedPath ? e.composedPath() : null;
    } catch (err) {
      path = null;
    }
    var el = path && path.length ? path[0] : e.target;
    if (el && el.nodeType !== 1) {
      el = el.parentElement || null; // 文本节点等
    }
    return el;
  }

  function attr(el, name) {
    var v = el.getAttribute && el.getAttribute(name);
    return v === null || v === undefined ? '' : String(v);
  }

  function truncate(text, max) {
    var t = (text || '').replace(/\s+/g, ' ').trim();
    return t.length > max ? t.slice(0, max) : t;
  }

  function explicitRole(el) {
    return attr(el, 'role').trim() || null;
  }

  function implicitRole(el) {
    var tag = el.nodeName.toLowerCase();
    if (tag === 'input') {
      var type = (attr(el, 'type') || 'text').toLowerCase();
      return INPUT_TYPE_ROLES[type] || 'textbox';
    }
    if (tag === 'a') return attr(el, 'href') ? 'link' : 'generic';
    return IMPLICIT_ROLES[tag] || null;
  }

  function roleOf(el) {
    return explicitRole(el) || implicitRole(el);
  }

  function accessibleName(el) {
    var labelledby = attr(el, 'aria-labelledby').trim();
    if (labelledby) {
      var parts = labelledby.split(/\s+/).map(function (id) {
        var ref = document.getElementById(id);
        return ref ? truncate(ref.textContent || '', MAX_TEXT_LEN) : '';
      });
      var joined = truncate(parts.join(' '), MAX_TEXT_LEN);
      if (joined) return joined;
    }
    var aria = truncate(attr(el, 'aria-label'), MAX_TEXT_LEN);
    if (aria) return aria;
    if (el.nodeName === 'IMG') {
      var alt = truncate(attr(el, 'alt'), MAX_TEXT_LEN);
      if (alt) return alt;
    }
    var text = truncate(el.textContent || '', MAX_TEXT_LEN);
    if (text) return text;
    // 敏感字段（password 等）的 value 不进 accessible name —— 与 maskedValue 同一红线
    var value = 'value' in el && typeof el.value === 'string' && !isSensitiveField(el)
      ? truncate(String(el.value), MAX_TEXT_LEN)
      : '';
    if (value) return value;
    var placeholder = truncate(attr(el, 'placeholder'), MAX_TEXT_LEN);
    if (placeholder) return placeholder;
    return truncate(attr(el, 'title'), MAX_TEXT_LEN);
  }

  function cssEscapeId(id) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(id);
    return String(id).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  function uniqueById(id) {
    try {
      return document.querySelectorAll('[id="' + id.replace(/"/g, '\\"') + '"]').length <= 1;
    } catch (e) {
      return false;
    }
  }

  function buildCss(el) {
    var ownId = attr(el, 'id').trim();
    if (ownId && uniqueById(ownId)) return '#' + cssEscapeId(ownId);
    var parts = [];
    var node = el;
    var depth = 0;
    while (node && node.nodeType === 1 && depth < 6) {
      var tag = node.nodeName.toLowerCase();
      var nodeId = attr(node, 'id').trim();
      if (nodeId && uniqueById(nodeId)) {
        parts.unshift('#' + cssEscapeId(nodeId));
        break;
      }
      var part = tag;
      var parent = node.parentElement;
      if (parent) {
        var same = 0;
        var idx = 0;
        for (var i = 0; i < parent.children.length; i++) {
          var child = parent.children[i];
          if (child.nodeName === node.nodeName) {
            same++;
            if (child === node) idx = same;
          }
        }
        if (same > 1) part += ':nth-of-type(' + idx + ')';
      }
      parts.unshift(part);
      node = parent;
      depth++;
    }
    return parts.join(' > ');
  }

  function buildXpath(el) {
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node.nodeName.toLowerCase() !== 'html') {
      var idx = 1;
      var sib = node.previousElementSibling;
      while (sib) {
        if (sib.nodeName === node.nodeName) idx++;
        sib = sib.previousElementSibling;
      }
      parts.unshift(node.nodeName.toLowerCase() + '[' + idx + ']');
      node = node.parentElement;
    }
    return '/html/' + parts.join('/');
  }

  function shadowPath(el) {
    var root = null;
    try {
      root = el.getRootNode ? el.getRootNode() : null;
    } catch (e) {
      return null;
    }
    if (!root || root === document || !root.host) return null;
    var parts = [];
    var node = el;
    var guard = 0;
    while (node && node.nodeType === 1 && guard < 12) {
      var desc = node.nodeName.toLowerCase();
      var nodeId = attr(node, 'id').trim();
      parts.unshift(nodeId ? desc + '#' + nodeId : desc);
      if (node === root.host) break;
      node = node.parentElement || (node.getRootNode ? node.getRootNode().host : null);
      guard++;
    }
    return parts.join(' / ');
  }

  function buildLocators(el) {
    var role = roleOf(el);
    var name = truncate(accessibleName(el), MAX_TEXT_LEN) || null;
    return {
      id: attr(el, 'id').trim() || null,
      testid: (attr(el, 'data-testid') || attr(el, 'data-test') || attr(el, 'data-qa')).trim() || null,
      role_name: role || name ? { role: role || null, name: name } : null,
      css: buildCss(el) || null,
      xpath: buildXpath(el) || null,
      text: truncate(el.textContent || '', MAX_TEXT_LEN) || null,
    };
  }

  function buildAttributes(el) {
    var keep = ['id', 'class', 'type', 'name', 'href', 'placeholder', 'alt', 'title', 'role', 'autocomplete', 'inputmode', 'aria-expanded', 'aria-haspopup'];
    var out = {};
    var attrs = el.attributes || [];
    for (var i = 0; i < attrs.length; i++) {
      var a = attrs[i];
      var n = a.name.toLowerCase();
      if (keep.indexOf(n) !== -1 || n.indexOf('data-') === 0) {
        var v = String(a.value);
        if (v.length <= MAX_ATTR_LEN && n !== 'value') out[n] = v; // value 一律不进属性（脱敏走事件 value 字段）
      }
    }
    return out;
  }

  function buildTarget(el) {
    if (!el) return null;
    var shadow = shadowPath(el);
    return {
      locators: buildLocators(el),
      tag: el.nodeName.toUpperCase(),
      role: roleOf(el),
      attributes: buildAttributes(el),
      in_iframe: false,
      shadow_path: shadow,
    };
  }

  function pixelInfo(e, el) {
    var rect = { left: 0, top: 0, width: 0, height: 0 };
    try {
      if (el && el.getBoundingClientRect) {
        var r = el.getBoundingClientRect();
        rect = { left: r.left, top: r.top, width: r.width, height: r.height };
      }
    } catch (err) { /* 布局不可用（detached） */ }
    var x = Number(e && e.clientX) || 0;
    var y = Number(e && e.clientY) || 0;
    var rx = rect.width > 0 ? Math.round(((x - rect.left) / rect.width) * 1000) / 1000 : 0;
    var ry = rect.height > 0 ? Math.round(((y - rect.top) / rect.height) * 1000) / 1000 : 0;
    return {
      viewport_point: { x: x, y: y },
      bbox: { x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) },
      rel_offset: { rx: rx, ry: ry },
    };
  }

  function pageInfo() {
    return {
      url: window.location.href,
      title: truncate(document.title || '', 200),
      viewport: { w: window.innerWidth || 0, h: window.innerHeight || 0 },
      dpr: window.devicePixelRatio || 1,
    };
  }

  function isSensitiveField(el) {
    if (!el) return false;
    var type = attr(el, 'type').toLowerCase();
    if (type === 'password') return true;
    var hints = [attr(el, 'name'), attr(el, 'id'), attr(el, 'autocomplete'), attr(el, 'placeholder')].join(' ');
    return SENSITIVE_NAME_RE.test(hints);
  }

  function maskedValue(el) {
    var raw = 'value' in el && el.value != null ? String(el.value) : '';
    if (isSensitiveField(el)) {
      return { length: raw.length }; // 安全红线：只记长度
    }
    return raw.length > MAX_VALUE_LEN ? raw.slice(0, MAX_VALUE_LEN) : raw;
  }

  // ------------------------------------------------------- 事件采集

  function handlePointEvent(type) {
    return function (e) {
      if (!state.enabled) return;
      var el = eventElement(e);
      if (!el) return;
      emit({
        type: type,
        page: pageInfo(),
        target: buildTarget(el),
        pixel: pixelInfo(e, el),
        value: null,
        page_effect: {},
      });
    };
  }

  // input/change：500ms debounce 合并为一次 fill，保留最终值（方案 6.1）
  function onInput(e) {
    if (!state.enabled) return;
    var el = eventElement(e);
    if (!el) return;
    if (el.nodeName === 'INPUT' && attr(el, 'type').toLowerCase() === 'file') return; // 走 file_change
    if (el.nodeName === 'INPUT' && (attr(el, 'type').toLowerCase() === 'checkbox' || attr(el, 'type').toLowerCase() === 'radio')) {
      emitFill(el); // 勾选类无最终值语义，立即出
      return;
    }
    var record = state.fillTimers.get(el);
    if (record && record.timer) {
      clearTimeout(record.timer);
    }
    record = record || { snapshot: buildTarget(el) };
    record.snapshot = buildTarget(el); // 定位信息取最近一次（DOM 可能动态变）
    record.timer = setTimeout(function () {
      record.timer = null;
      emitFill(el, record.snapshot);
    }, INPUT_DEBOUNCE_MS);
    state.fillTimers.set(el, record);
  }

  function emitFill(el, snapshot) {
    if (!state.enabled) return;
    var target = snapshot || buildTarget(el);
    emit({
      type: 'fill',
      page: pageInfo(),
      target: target,
      pixel: null,
      value: maskedValue(el),
      page_effect: {},
    });
  }

  function onChange(e) {
    if (!state.enabled) return;
    var el = eventElement(e);
    if (!el) return;
    if (el.nodeName === 'INPUT' && attr(el, 'type').toLowerCase() === 'file') {
      var names = [];
      try {
        if (el.files) {
          for (var i = 0; i < el.files.length; i++) names.push(el.files[i].name); // 只记文件名
        }
      } catch (err) { /* 文件句柄受限 */ }
      emit({
        type: 'file_change',
        page: pageInfo(),
        target: buildTarget(el),
        pixel: null,
        value: { files: names },
        page_effect: {},
      });
      return;
    }
    var record = el && state.fillTimers.get ? state.fillTimers.get(el) : null;
    if (record && record.timer) {
      clearTimeout(record.timer); // change 即最终值，立即合并出一次 fill
      record.timer = null;
    }
    if (el.nodeName === 'SELECT' || el.nodeName === 'TEXTAREA' || el.nodeName === 'INPUT') {
      emitFill(el);
    }
  }

  // keydown：仅功能键与快捷键组合（方案 6.1）
  function onKeyDown(e) {
    if (!state.enabled) return;
    var key = e.key || '';
    var isModifier = Object.prototype.hasOwnProperty.call(MODIFIER_KEYS, key);
    var combo = e.ctrlKey || e.metaKey || e.altKey;
    var functional = Object.prototype.hasOwnProperty.call(FUNCTION_KEYS, key) || /^F([1-9]|1[0-2])$/.test(key);
    if (isModifier) return;
    if (!functional && !combo) return;
    var parts = [];
    if (e.ctrlKey || e.metaKey) parts.push('ctrl');
    if (e.altKey) parts.push('alt');
    if (e.shiftKey) parts.push('shift');
    parts.push(key);
    var el = eventElement(e);
    emit({
      type: 'key',
      page: pageInfo(),
      target: el ? buildTarget(el) : null,
      pixel: el ? pixelInfo(e, el) : null,
      value: parts.join('+'),
      page_effect: {},
    });
  }

  function onSubmit(e) {
    if (!state.enabled) return;
    var el = eventElement(e);
    if (!el) return;
    emit({
      type: 'submit',
      page: pageInfo(),
      target: buildTarget(el),
      pixel: null,
      value: null,
      page_effect: {},
    });
  }

  // scroll：300ms 节流，记录滚动容器与 scrollTop/Left（方案 6.1）
  function onScroll(e) {
    if (!state.enabled) return;
    var node = e.target;
    if (!node) return;
    if (node === document || node === document.documentElement || node === document.body) {
      node = document.scrollingElement || document.documentElement;
    }
    if (!node || node.nodeType !== 1) return;
    var now = Date.now();
    var last = state.scrollLastAt.get(node) || 0;
    if (now - last < SCROLL_THROTTLE_MS) return;
    state.scrollLastAt.set(node, now);
    var container = node === (document.scrollingElement || document.documentElement)
      ? 'window'
      : buildCss(node);
    emit({
      type: 'scroll',
      page: pageInfo(),
      target: node === (document.scrollingElement || document.documentElement)
        ? null
        : buildTarget(node),
      pixel: null,
      value: {
        container: container,
        scroll_top: Math.round(node.scrollTop || 0),
        scroll_left: Math.round(node.scrollLeft || 0),
      },
      page_effect: {},
    });
  }

  // ------------------------------------------------------------ 导航采集

  function emitNavigate(fromUrl, toUrl) {
    if (!state.enabled) return;
    emit({
      type: 'navigate',
      page: pageInfo(),
      target: null,
      pixel: null,
      value: { from: fromUrl || null, to: toUrl || null },
      page_effect: { navigated_to: toUrl || null },
    });
    scheduleScan();
  }

  function rememberUrl() {
    state.lastUrl = window.location.href;
    safeStorage('set', LAST_URL_KEY, state.lastUrl);
  }

  function onPageshow() {
    if (!state.enabled) {
      rememberUrl();
      return;
    }
    var current = window.location.href;
    if (state.lastUrl && state.lastUrl !== current) {
      emitNavigate(state.lastUrl, current);
    } else {
      scheduleScan();
    }
    rememberUrl();
  }

  function patchHistory() {
    var push = history.pushState;
    var replace = history.replaceState;
    function wrapped(original, name) {
      return function () {
        var from = window.location.href;
        var result = original.apply(this, arguments);
        var to = window.location.href;
        try {
          var argUrl = arguments[2];
          if (argUrl) to = new URL(String(argUrl), window.location.href).href;
        } catch (e) { /* 相对 URL 解析失败则用当前 */
        }
        if (state.enabled) emitNavigate(from, to);
        rememberUrl();
        return result;
      };
    }
    try {
      history.pushState = wrapped(push);
      history.replaceState = wrapped(replace);
    } catch (e) {
      /* history 不可改写（极端环境）：popstate/pageshow 兜底 */
    }
  }

  // ------------------------------------------- 页面轻量扫描与 DOM 指纹（6.4③）

  function scanInteractive() {
    var nodes = document.querySelectorAll(INTERACTIVE_SELECTOR);
    var parts = [];
    var elements = [];
    var limit = Math.min(nodes.length, MAX_SCAN_ELEMENTS);
    for (var i = 0; i < limit; i++) {
      var el = nodes[i];
      var role = roleOf(el) || '';
      var name = truncate(accessibleName(el), MAX_TEXT_LEN);
      var href = truncate(attr(el, 'href'), MAX_TEXT_LEN);
      parts.push([el.nodeName.toLowerCase(), role, name, href].join('|'));
      if (elements.length < MAX_SCAN_ELEMENTS) {
        elements.push({ tag: el.nodeName.toLowerCase(), role: role, name: name, href: href });
      }
    }
    return {
      dom_hash: sha1hex(parts.join('\n')),
      interactive_count: nodes.length,
      interactive_elements: elements,
    };
  }

  function scheduleScan() {
    if (!isTop) return;
    if (state.scanTimer) clearTimeout(state.scanTimer);
    state.scanTimer = setTimeout(function () {
      state.scanTimer = null;
      if (!state.enabled) return;
      var scan = scanInteractive();
      emit({
        type: 'page_scan',
        page: pageInfo(),
        target: null,
        pixel: null,
        value: null,
        page_effect: {
          dom_hash: scan.dom_hash,
          interactive_count: scan.interactive_count,
          interactive_elements: scan.interactive_elements,
        },
      });
    }, SCAN_SETTLE_MS);
  }

  // ---------------------------------------------------- MutationObserver 计数

  try {
    var observer = new MutationObserver(function (records) {
      state.mutationCount += records.length;
    });
    var startObserve = function () {
      try {
        observer.observe(document, { childList: true, subtree: true, attributes: true, characterData: true });
      } catch (e) { /* 文档未就绪：DOM ready 后再挂 */
        document.addEventListener('DOMContentLoaded', function () {
          observer.observe(document, { childList: true, subtree: true, attributes: true, characterData: true });
        });
      }
    };
    startObserve();
  } catch (e) {
    /* MutationObserver 不可用：dom_mutation_count 恒为 0 */
  }

  // ------------------------------------------------------------ 挂载监听

  window.addEventListener('click', handlePointEvent('click'), true);
  window.addEventListener('dblclick', handlePointEvent('dblclick'), true);
  window.addEventListener('input', onInput, true);
  window.addEventListener('change', onChange, true);
  window.addEventListener('keydown', onKeyDown, true);
  window.addEventListener('submit', onSubmit, true);
  window.addEventListener('scroll', onScroll, true);
  window.addEventListener('pageshow', onPageshow);
  window.addEventListener('popstate', function () {
    if (!state.enabled) {
      rememberUrl();
      return;
    }
    var from = state.lastUrl;
    rememberUrl();
    emitNavigate(from, window.location.href);
  });
  window.addEventListener('hashchange', function () {
    if (!state.enabled) return;
    emitNavigate(state.lastUrl, window.location.href);
    rememberUrl();
  });

  patchHistory();

  // ------------------------------------------------------------ sha1（DOM 指纹）
  // 纯 JS SHA-1（hex 输出）：非安全上下文（http/file）无 crypto.subtle，自实现以
  // 保证 dom_hash 三端一致。仅用于 DOM 指纹，不承担密码学安全职责。

  function sha1hex(str) {
    function utf8Bytes(input) {
      var out = [];
      for (var i = 0; i < input.length; i++) {
        var c = input.charCodeAt(i);
        if (c < 0x80) out.push(c);
        else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
        else if (c >= 0xd800 && c <= 0xdbff && i + 1 < input.length) {
          var c2 = input.charCodeAt(++i);
          var cp = ((c - 0xd800) << 10) + (c2 - 0xdc00) + 0x10000;
          out.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 63), 0x80 | ((cp >> 6) & 63), 0x80 | (cp & 63));
        } else out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
      }
      return out;
    }
    function rotl(n, s) { return ((n << s) | (n >>> (32 - s))) | 0; }
    var bytes = utf8Bytes(String(str));
    var len = bytes.length;
    var words = [];
    for (var i = 0; i < len; i++) words[i >> 2] = (words[i >> 2] || 0) | (bytes[i] << (24 - (i % 4) * 8));
    words[len >> 2] = (words[len >> 2] || 0) | (0x80 << (24 - (len % 4) * 8));
    var blocks = (((len + 8) >> 6) + 1) * 16;
    words[blocks - 1] = len * 8;
    var H = [0x67452301 | 0, 0xefcdab89 | 0, 0x98badcfe | 0, 0x10325476 | 0, 0xc3d2e1f0 | 0];
    var w = new Array(80);
    for (var b = 0; b < blocks / 16; b++) {
      for (var t = 0; t < 16; t++) w[t] = words[b * 16 + t] || 0;
      for (var t2 = 16; t2 < 80; t2++) w[t2] = rotl(w[t2 - 3] ^ w[t2 - 8] ^ w[t2 - 14] ^ w[t2 - 16], 1);
      var a = H[0], bb = H[1], c = H[2], d = H[3], e = H[4];
      for (var t3 = 0; t3 < 80; t3++) {
        var f, k;
        if (t3 < 20) { f = (bb & c) | (~bb & d); k = 0x5a827999 | 0; }
        else if (t3 < 40) { f = bb ^ c ^ d; k = 0x6ed9eba1 | 0; }
        else if (t3 < 60) { f = (bb & c) | (bb & d) | (c & d); k = 0x8f1bbcdc | 0; }
        else { f = bb ^ c ^ d; k = 0xca62c1d6 | 0; }
        var tmp = (rotl(a, 5) + f + e + k + w[t3]) | 0;
        e = d; d = c; c = rotl(bb, 30); bb = a; a = tmp;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + bb) | 0; H[2] = (H[2] + c) | 0;
      H[3] = (H[3] + d) | 0; H[4] = (H[4] + e) | 0;
    }
    var hex = '';
    for (var j = 0; j < 5; j++) hex += ('00000000' + ((H[j] >>> 0).toString(16))).slice(-8);
    return hex;
  }

  // ------------------------------------------------------------ 控制协议

  window.__qaRecorderSetEnabled = function (enabled) {
    var prev = state.enabled;
    state.enabled = !!enabled;
    if (state.enabled && !prev) {
      flushPending();
      rememberUrl();
      scheduleScan(); // 开始采集即记录当前页面指纹
    }
    return state.enabled;
  };

  window.__qaRecorderGetState = function () {
    return {
      enabled: state.enabled,
      seq: state.seq,
      lastUrl: state.lastUrl,
      pending: state.pending.length,
      top: isTop,
    };
  };

  window.__qaRecorderScan = function () {
    return scanInteractive();
  };

  window.__qaRecorderFlush = flushPending;

  window.__qaRecorderSha1 = sha1hex; // 测试/诊断用（与宿主对账 dom_hash）
})();
