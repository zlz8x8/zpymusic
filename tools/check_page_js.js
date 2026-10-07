/* 用最小 DOM stub 在 node 里跑一遍"曲谱页面"的 JS（重点是 M7 的小节高亮）。
 *
 * 为什么要它：WebEngine 后端在本机（受限沙箱）跑不起来 —— Chromium 需要命名管道
 * IPC，会直接 FATAL abort，没法用 pytest 覆盖。而 M7 把高亮策略改成"跟随当前小节"后，
 * 页面侧新增了 setMeasure/clearMeasure（用 getBBox 取小节矩形、插一个 <rect>）。
 * 这里用一个只有十几个方法的 DOM stub 把这段逻辑真正跑一遍：
 *
 *   * 矩形位置/尺寸是否按 getBBox 算出（含外扩）
 *   * 反复切换小节时旧矩形是否被移除（不累积）
 *   * 页面里找不到该小节时是否回退到逐音符高亮（setActive）
 *   * 小节在视口舒适区之外时是否触发跟随滚动
 *
 * 用法（由 tests/test_measure_highlight.py 调用；也可手动跑）::
 *
 *     python -c "from zpymusic.sync.local_server import PAGE_HTML; \
 *                open('page.html','w',encoding='utf-8').write(PAGE_HTML)"
 *     node tools/check_page_js.js page.html
 */

"use strict";

const fs = require("fs");

const pagePath = process.argv[2];
if (!pagePath) {
  console.error("用法: node check_page_js.js <page.html>");
  process.exit(2);
}
const html = fs.readFileSync(pagePath, "utf8");
const start = html.indexOf("<script>") + "<script>".length;
const end = html.indexOf("</script>", start);
const code = html.slice(start, end);

// ---------------------------------------------------------------- DOM stub
class El {
  constructor(tag, id) {
    this.tagName = (tag || "").toUpperCase();
    this.id = id || "";
    this.children = [];
    this.parentNode = null;
    this.attrs = {};
    this.classList = {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    };
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    // 真实浏览器里 setAttribute("class", …) 会同步 classList，stub 必须一致
    if (k === "class") {
      this.classList._s = new Set(String(v).split(/\s+/).filter(Boolean));
    }
  }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  getBBox() { return this._bbox || { x: 0, y: 0, width: 0, height: 0 }; }
  getBoundingClientRect() {
    return this._r || { top: 100, bottom: 200, left: 0, right: 100 };
  }
  closest(sel) {
    let n = this;
    while (n) {
      if (sel === "g.measure" && n.classList.contains("measure")) return n;
      if (sel === ".sys" && n.classList.contains("sys")) return n;
      n = n.parentNode;
    }
    return null;
  }
}

const registry = new Map();
function reg(el) { if (el.id) registry.set(el.id, el); return el; }

const svg = new El("svg", "root");
const sys = reg(new El("div", "sys-1"));
sys.classList.add("sys");
const measure = reg(new El("g", "m1"));
measure.classList.add("measure");
measure._bbox = { x: 100, y: 200, width: 400, height: 300 };
// 故意让当前小节落在视口舒适区之外，才能验证"跟随滚动"
sys._r = { top: 900, bottom: 1100, left: 0, right: 100 };
measure._r = { top: 900, bottom: 1000, left: 0, right: 100 };
svg.appendChild(measure);
sys.appendChild(svg);

global.document = {
  documentElement: { style: { setProperty() {} } },
  head: new El("head"),
  body: new El("body"),
  createElement: (t) => new El(t),
  createElementNS: (_ns, t) => new El(t),
  getElementById: (id) => registry.get(id) || null,
  querySelectorAll: () => [],
};
global.window = {
  innerHeight: 800,
  scrollY: 0,
  scrollTo(o) { global.window.scrollY = o.top; },
};
global.IntersectionObserver = class { observe() {} disconnect() {} };
global.fetch = () => Promise.reject(new Error("stub 里没有网络"));

// 执行页面 JS
eval(code); // eslint-disable-line no-eval

// ---------------------------------------------------------------- 断言
let failures = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? "OK " : "FAIL"}] ${name} ${detail === undefined ? "" : detail}`);
  if (!ok) failures++;
}
const measureRects = () =>
  measure.children.filter((c) => c.classList.contains("zpy-hl-measure"));

check("页面初始化出 zpy API", typeof window.zpy.setMeasure === "function");

// 1) 正常小节高亮：矩形 = getBBox + 外扩
check("setMeasure 命中小节", window.zpy.setMeasure("m1", ["n1"], true) === true);
const rect = measureRects()[0];
check("小节容器里插入了覆盖矩形", !!rect);
if (rect) {
  const pad = Math.max(1, 300 * 0.02);
  check("矩形 x/y 有外扩",
    Number(rect.getAttribute("x")) === 100 - pad &&
    Number(rect.getAttribute("y")) === 200 - pad);
  check("矩形宽度 = 小节宽度 + 2×pad",
    Number(rect.getAttribute("width")) === 400 + 2 * pad, rect.getAttribute("width"));
  check("矩形高度 ≈ 谱表高度 + 2×pad",
    Number(rect.getAttribute("height")) === 300 + 2 * pad, rect.getAttribute("height"));
  check("矩形不拦截鼠标（点击跳转仍可用）",
    rect.getAttribute("class") === "zpy-hl-measure");
}
check("小节在舒适区外时触发跟随滚动", window.scrollY > 0, String(window.scrollY));

// 2) 切换小节：旧矩形必须被移除（不能越积越多）
window.zpy.setMeasure("m1", ["n2"], false);
check("重复设置不累积矩形", measureRects().length === 1);

// 3) 找不到小节 → 回退逐音符高亮
measure.children.length = 0;
const calls = [];
window.zpy.setActive = (ids) => { calls.push(ids); return ids.length; };
const ok3 = window.zpy.setMeasure("不存在的小节", ["n1"], false);
check("未知小节返回 false", ok3 === false);
check("未知小节回退到 setActive(ids)",
  calls.length > 0 && JSON.stringify(calls[calls.length - 1]) === '["n1"]',
  JSON.stringify(calls));
check("未知小节不插入矩形", measureRects().length === 0);

// 4) clearMeasure 可重复调用
window.zpy.clearMeasure();
window.zpy.clearMeasure();
check("clearMeasure 可重复调用", true);

console.log(failures ? `\n${failures} 项失败` : "\n全部通过");
process.exit(failures ? 1 : 0);
