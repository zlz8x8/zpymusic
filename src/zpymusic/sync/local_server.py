"""本地 HTTP 服务，为 WebEngine 后端提供套件资源（需求 §5.3 / D5）。

**为什么需要**：WebEngine 后端要在页面里 ``fetch()`` 各个 system 的 SVG。
两条路可选：

* 用 ``QWebChannel`` 把 SVG 文本从 Python 推给 JS —— 一个 system 就是几百 KB，
  交响乐有 276 个，序列化与 JS 解析都会很卡；
* 起一个只读的本地 HTTP 服务 —— 浏览器走正常的 HTTP 缓存，Python 侧零开销。

选后者。安全上做了三重限制：

1. 只绑定 ``127.0.0.1``，端口由系统随机分配；
2. 只允许访问**一个套件目录**下的文件（路径穿越会被拒绝）；
3. 只允许 ``.svg`` / ``.html`` / 音频扩展名，且全部以 ``application/octet-stream``
   之外的正确 MIME 返回。

音频也走这个服务（``/audio``），这样 ``QMediaPlayer`` 能靠 HTTP Range 请求自由 seek。
"""

from __future__ import annotations

import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..common.log import get_logger

log = get_logger(__name__)

__all__ = ["LocalAssetServer", "PAGE_HTML"]

_ALLOWED_SUFFIXES = frozenset(
    {".svg", ".html", ".htm", ".css", ".js", ".mp3", ".mid", ".midi", ".wav", ".json"}
)


def _rel_path(rest: str) -> Path:
    """URL 路径里的一段 → 相对资源根目录的 :class:`Path`。

    ``rest`` 是 ``unquote`` 之后的值，可能还带子目录（``svg/sys-0001.svg``）。
    **不做**任何"去掉 .."的清理：越界由 :meth:`_Handler._resolve` 统一拒绝
    （``resolve()`` 之后必须仍在根目录内）。
    """
    return Path(rest)

#: WebEngine 后端加载的页面（内嵌，避免额外资源文件）
PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>zpyMusic 曲谱</title>
<style>
  html, body { margin:0; padding:0; background:#f5f5f5; }
  body { font-family: "Segoe UI", system-ui, sans-serif; }
  #systems { display:flex; flex-direction:column; align-items:center; gap:14px;
             padding:14px 0 40px; }
  /* 谱面比视口宽时（关掉"适应宽度"或放大到 100% 以上），普通的 center 会把
     溢出部分**平均分到两侧**，导致左侧那一截被裁掉且滚不回去（实测：
     2100px 谱面 / 1200px 视口时 .sys 的 left = -458，scrollWidth 只有 1642 ——
     最左边 458px 永远看不到）。safe center 在溢出时退回 start，保证左边缘可达；
     不支持 safe 关键字的浏览器会保留上一条 center。 */
  #systems { align-items: safe center; }
  .sys { position:relative; width:max-content; line-height:0;
         background:#fff; box-shadow:0 1px 3px rgba(0,0,0,.12); }
  .sys > svg { display:block; height:auto; max-width:100%; }
  .sys.pending::after { content:"载入中…"; position:absolute; inset:0;
         display:flex; align-items:center; justify-content:center;
         color:#bbb; font-size:11pt; line-height:1; }
  .sys.error::after { content:"该行加载失败"; color:#c33; }
  /* ---- 高亮（颜色/透明度由 Python 注入 CSS 变量）---- */
  g.note.zpy-hl, g.chord.zpy-hl, g.rest.zpy-hl {
      fill: var(--zpy-hl-color, #FF8C00) !important;
      color: var(--zpy-hl-color, #FF8C00) !important;
      opacity: var(--zpy-hl-alpha, .35);
  }
  g.chord.zpy-hl > g.note { fill: var(--zpy-hl-color, #FF8C00) !important;
      color: var(--zpy-hl-color, #FF8C00) !important; }
  g.note, g.chord, g.rest { transition: fill 80ms linear, opacity 80ms linear; }
  /* ---- 小节高亮：盖住整个小节的半透明矩形（不挡鼠标，点击跳转仍可用）---- */
  rect.zpy-hl-measure {
      fill: var(--zpy-hl-color, #FF8C00) !important;
      stroke: var(--zpy-hl-color, #FF8C00) !important;
      stroke-width: 1;
      opacity: var(--zpy-hl-alpha, .35);
      pointer-events: none;
  }
  #empty { padding:40px; text-align:center; color:#888; font-size:12pt; }
</style>
</head>
<body>
<div id="empty">尚未载入套件</div>
<div id="systems"></div>
<script>
"use strict";
window.__zpyState = { ready:false, mounted:[], active:[], server:0, measure:null };

function baseUrl() { return window.__zpyBase || ""; }

/* 行 SVG 的 URL：file 是**相对套件目录**的路径（如 "svg/sys-0001.svg"）。
   逐段 encodeURIComponent —— 这样 "svg" 与文件名里的空格/特殊字符都能安全传输，
   同时保留目录分隔符（整串编码会把 "/" 编成 %2F，服务端就找不到文件）。
   服务端 /sys/<相对路径> 就是按这个约定解析的。 */
function systemUrl(file) {
  return baseUrl() + "/sys/" + String(file).split("/").map(encodeURIComponent).join("/");
}

function makeSystemBlock(index, file, width, height) {
  const d = document.createElement("div");
  d.className = "sys pending";
  d.id = "sys-" + index;
  d.dataset.index = index;
  d.dataset.file = file;
  d.dataset.src = systemUrl(file);
  if (width)  d.style.minHeight = Math.round(height || 0) + "px";
  d.dataset.loaded = "0";
  return d;
}

async function loadSystem(index) {
  const d = document.getElementById("sys-" + index);
  if (!d || d.dataset.loaded === "1" || d.dataset.loading === "1") return false;
  d.dataset.loading = "1";
  const url = d.dataset.src;
  try {
    let res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status + " " + res.statusText);
    let text = await res.text();
    if (!text || text.indexOf("<svg") < 0) {
      throw new Error("响应不是 SVG（前 60 字节：" + text.slice(0, 60) + "）");
    }
    d.innerHTML = text;
    d.dataset.loaded = "1";
    d.classList.remove("pending", "error");
    d.dataset.err = "";
    return true;
  } catch (e) {
    // 失败一次后重试一次（首次加载时服务/页面可能还没完全就绪）
    try {
      await new Promise(r => setTimeout(r, 400));
      const res2 = await fetch(url, { cache: "no-store" });
      if (!res2.ok) throw new Error("HTTP " + res2.status);
      const text2 = await res2.text();
      d.innerHTML = text2;
      d.dataset.loaded = "1";
      d.classList.remove("pending", "error");
      return true;
    } catch (e2) {
      d.classList.remove("pending");
      d.classList.add("error");
      d.dataset.err = String(e2 && e2.message ? e2.message : e2);
      console.error("[zpy] 行 " + index + " 加载失败", url, e2);
      return false;
    }
  } finally {
    d.dataset.loading = "0";
  }
}

/* 供排障：把每行失败的原因汇总出来，Python 侧可直接读 */
window.zpyErrors = function () {
  return Array.from(document.querySelectorAll(".sys.error")).map(d => ({
    index: d.dataset.index, url: d.dataset.src, err: d.dataset.err || ""
  }));
};

/* 懒加载：只挂载视口附近的行 */
let io = null;
function observeSystems() {
  if (io) io.disconnect();
  io = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (en.isIntersecting) loadSystem(en.target.dataset.index);
    }
  }, { rootMargin: "150% 0px" });
  document.querySelectorAll(".sys").forEach(el => io.observe(el));
}

window.zpy = {
  setSystems: function (list) {
    const host = document.getElementById("systems");
    host.innerHTML = "";
    document.getElementById("empty").style.display = list.length ? "none" : "block";
    for (const s of list) {
      host.appendChild(makeSystemBlock(s.index, s.file, s.width, s.height));
    }
    window.__zpyState.mounted = [];
    window.__zpyState.measure = null;   // 旧的小节覆盖框已随 innerHTML 清掉
    observeSystems();
    return list.length;
  },
  setBase: function (b) { window.__zpyBase = b; return b; },
  setHighlightStyle: function (color, alpha) {
    document.documentElement.style.setProperty("--zpy-hl-color", color);
    document.documentElement.style.setProperty("--zpy-hl-alpha", alpha);
    return true;
  },
  setActive: function (ids) {
    const cls = "zpy-hl";
    const prev = window.__zpyState.active || [];
    const next = ids || [];
    for (const id of prev) {
      if (next.indexOf(id) >= 0) continue;
      const el = document.getElementById(id);
      if (el) el.classList.remove(cls);
    }
    for (const id of next) {
      if (prev.indexOf(id) >= 0) continue;
      let el = document.getElementById(id);
      if (!el) {
        // 展开 ID 回退到 base
        const base = id.replace(/-rend\\d*$/, "");
        el = document.getElementById(base);
      }
      if (el) {
        el.classList.add(cls);
        const chord = el.closest("g.chord");
        if (chord) chord.classList.add(cls);
      }
    }
    window.__zpyState.active = next.slice();
    return next.length;
  },
  clearHighlight: function () {
    window.zpy.clearMeasure();
    document.querySelectorAll(".zpy-hl").forEach(el => el.classList.remove("zpy-hl"));
    window.__zpyState.active = [];
    return true;
  },
  /* ---- 小节高亮（M7 起的主策略）----
     高亮"当前播放的小节"：矩形宽度 = 小节宽度、高度 ≈ 该行谱表高度。
     矩形直接插进小节的 <g class="measure"> 里，坐标取 g.getBBox() ——
     它是 g 自身用户坐标系里的值，因此 viewBox / 页面缩放 / 父级 transform
     全都不用自己换算（插进去的子元素与谱面内容共享同一套坐标）。
     ids 是当前发声的音符：万一页面里找不到该小节（sync.json 与 SVG 不同源），
     自动退回逐音符高亮 —— 与原生后端一致，绝不"整首都没有高亮"。
     旧的逐音符高亮仍保留（没有小节数据的旧套件回退用）。 */
  setMeasure: function (id, ids, follow) {
    const fallback = function () {
      if (ids && ids.length) window.zpy.setActive(ids);
      return false;
    };
    window.zpy.clearMeasure();
    window.zpy.setActive([]);   // 两种高亮模式互斥
    if (!id) return fallback();
    // ID 变体：反复记号会把同一小节展开成 xxx 与 xxx-rend2，而 SVG 里只画其中一个
    const base = id.replace(/-rend\\d*$/, "");
    let g = document.getElementById(id) || (base !== id ? document.getElementById(base) : null);
    if (!g) return fallback();
    if (!(g.classList && g.classList.contains("measure"))) {
      const m = g.closest ? g.closest("g.measure") : null;
      if (!m) return fallback();
      g = m;
    }
    let bb = null;
    try { bb = g.getBBox(); } catch (e) { bb = null; }
    if (!bb || !(bb.width > 0) || !(bb.height > 0)) return fallback();
    const pad = Math.max(1, bb.height * 0.02);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("class", "zpy-hl-measure");
    rect.setAttribute("x", bb.x - pad);
    rect.setAttribute("y", bb.y - pad);
    rect.setAttribute("width", bb.width + 2 * pad);
    rect.setAttribute("height", bb.height + 2 * pad);
    rect.setAttribute("rx", 4);
    g.appendChild(rect);
    window.__zpyState.measure = rect;
    if (follow) window.zpy.followTo(g.id || id);
    return true;
  },
  clearMeasure: function () {
    const rect = window.__zpyState.measure;
    if (rect && rect.parentNode) rect.parentNode.removeChild(rect);
    window.__zpyState.measure = null;
    return true;
  },
  scrollToId: function (id, center) {
    let el = document.getElementById(id);
    if (!el) el = document.getElementById(id.replace(/-rend\\d*$/, ""));
    if (!el) return false;
    el.scrollIntoView({ block: center === false ? "nearest" : "center", behavior: "auto" });
    return true;
  },
  /* 跟随滚动：只在元素离开视口"舒适区"时才滚动，并把当前行顶端放到上部 1/4 处。
     旧的 scrollIntoView({block:"center"}) 每帧强行居中，会让用户"滚不回顶端"。*/
  followTo: function (id) {
    let el = document.getElementById(id);
    if (!el) el = document.getElementById(id.replace(/-rend\\d*$/, ""));
    if (!el) return false;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    if (vh <= 0) return false;
    const box = el.getBoundingClientRect();
    const band = vh * 0.12;
    if (box.top >= band && box.bottom <= vh - band) return true;   // 还在舒适区：不动
    const sys = el.closest(".sys");
    const top = (sys ? sys.getBoundingClientRect().top : box.top) + window.scrollY;
    window.scrollTo({ top: Math.max(0, top - vh * 0.25), behavior: "auto" });
    return true;
  },
  scrollToSystem: function (index, center) {
    const el = document.getElementById("sys-" + index);
    if (!el) return false;
    el.scrollIntoView({ block: center === false ? "nearest" : "center", behavior: "auto" });
    return true;
  },
  querySystemAtPoint: function (x, y) {
    const el = document.elementFromPoint(x, y);
    if (!el) return null;
    const sys = el.closest(".sys");
    return sys ? parseInt(sys.dataset.index, 10) : null;
  },
  /* 命中测试：返回点击位置的元素 ID（优先面积最小的可高亮元素） */
  hitTest: function (x, y) {
    const stack = document.elementsFromPoint(x, y) || [];
    for (const el of stack) {
      const g = el.closest("g.note, g.chord, g.rest");
      if (g && g.id) return g.id;
    }
    return null;
  },
  loadSystem: loadSystem,
  mountCount: function () {
    return Array.from(document.querySelectorAll(".sys"))
      .filter(d => d.dataset.loaded === "1").length;
  },
  systemCount: function () { return document.querySelectorAll(".sys").length; }
};
window.__zpyState.ready = true;
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    """只读资源处理器。"""

    server_version = "zpyMusic/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.debug("HTTP %s", fmt % args)

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:  # noqa: N802 - http.server 命名
        root: Path = self.server.asset_root  # type: ignore[attr-defined]
        page: bytes = self.server.page_bytes  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in ("/", "/index.html"):
            self._send_bytes(page, "text/html; charset=utf-8")
            return

        if path.startswith("/svg/"):
            name = path[len("/svg/") :]
            self._send_file(root / "svg" / name)
            return

        if path.startswith("/sys/"):
            # 行 SVG 的**相对套件目录**路径（``svg/sys-0001.svg``）。
            # 套件与预览缓存的目录层级可能不同（预览缓存把行放在 <cache>/svg/ 下
            # 只是为了共用 /svg/ 这条路由），所以不能假设一定在 svg/ 里。
            # 越界与后缀白名单由 :meth:`_resolve` 兜住。
            self._send_file(root / _rel_path(path[len("/sys/") :]))
            return

        if path.startswith("/audio"):
            name = path[len("/audio") :].lstrip("/")
            if not name:
                self._send_error(400, "缺少音频文件名")
                return
            self._send_file(root / name, allow_range=True)
            return

        if path.startswith("/file/"):
            self._send_file(root / path[len("/file/") :], allow_range=True)
            return

        self._send_error(404, "未找到")

    # ------------------------------------------------------------------ 辅助
    def _resolve(self, candidate: Path) -> Path | None:
        """校验路径确实落在套件目录内（防目录穿越）。"""
        root: Path = self.server.asset_root  # type: ignore[attr-defined]
        try:
            resolved = candidate.resolve()
            root_resolved = root.resolve()
        except OSError:
            return None
        if root_resolved != resolved and root_resolved not in resolved.parents:
            log.warning("拒绝越界访问：%s", candidate)
            return None
        if resolved.suffix.lower() not in _ALLOWED_SUFFIXES:
            log.warning("拒绝访问非白名单类型：%s", resolved.name)
            return None
        if not resolved.is_file():
            return None
        return resolved

    def _send_file(self, candidate: Path, *, allow_range: bool = False) -> None:
        path = self._resolve(candidate)
        if path is None:
            self._send_error(404, "未找到")
            return
        try:
            data = path.read_bytes()
        except OSError as e:
            self._send_error(500, f"读取失败：{e}")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() == ".svg":
            ctype = "image/svg+xml"
        if allow_range and self.headers.get("Range"):
            self._send_range(data, ctype)
        else:
            self._send_bytes(data, ctype)

    def _send_range(self, data: bytes, ctype: str) -> None:
        """支持 Range（音频 seek 必需）。"""
        raw = self.headers.get("Range", "")
        try:
            spec = raw.split("=", 1)[1]
            start_s, _, end_s = spec.partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else len(data) - 1
            end = min(end, len(data) - 1)
            if start > end or start >= len(data):
                raise ValueError("bad range")
        except (ValueError, IndexError):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{len(data)}")
            self.end_headers()
            return
        chunk = data[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(chunk)

    def _send_bytes(self, data: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, code: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalAssetServer:
    """在后台线程里跑的只读资源服务。

    服务**只启动一次**，切换套件时用 :meth:`set_root` 换目录 ——
    不重启服务、不换端口。原因：WebEngine 的页面是长期存活的，
    如果每次切换套件都"停旧服务 → 起新服务（新端口）"，
    页面就要在运行中改 origin，极易出现"资源请求失败"（用户实测报
    "该行加载失败"，而服务端实测 448/448 全部 200，问题出在浏览器侧）。
    保持同一端口后，只要首次能加载，后续切换就必然能加载。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._httpd is None:
            return 0
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def set_root(self, root: Path) -> None:
        """切换服务的根目录（切换套件时用）。

        若服务尚未启动，则只记录目录；否则同步更新，使后续请求指向新套件。
        """
        self.root = Path(root)
        if self._httpd is not None:
            self._httpd.asset_root = self.root  # type: ignore[attr-defined]
        log.info("资源服务根目录已切换为 %s", self.root)

    def start(self) -> str:
        if self._httpd is not None:
            return self.base_url
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.daemon_threads = True
        httpd.asset_root = self.root  # type: ignore[attr-defined]
        httpd.page_bytes = PAGE_HTML.encode("utf-8")  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="zpy-assets", daemon=True)
        self._thread.start()
        log.info("本地资源服务已启动：%s（根目录 %s）", self.base_url, self.root)
        return self.base_url

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        log.debug("本地资源服务已停止")

    def __enter__(self) -> "LocalAssetServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
