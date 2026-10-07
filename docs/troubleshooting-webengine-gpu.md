# 案例与手册：QtWebEngine 的 GPU 报错

> **适用对象**：本项目后续维护者，以及任何遇到"Qt / Chromium 报 GPU 上下文错误，但显卡明明正常"的人。
> **写入时间**：2026-10（M6）。**状态**：已定位、已修复、已有回归测试。
> **代码索引**：`src/zpymusic/sync/web_view.py`、`tests/test_webengine_env.py`、`tools/probes/probe_webengine_perf.py`、`tools/diagnose_web_switch.py`。
> 需求文档侧的记录见 `docs/requirements.md` §12.11；面向使用者的简版见 `README.md`「排障：浏览器引擎的 GPU 报错」。

---

## 1. 一句话结论

那两条看起来像"显卡不行"的报错，**是本项目自己在启动时注入 `--disable-gpu` 和
`QT_QUICK_BACKEND=software` 造成的**，而且是**非致命**的。

真正让 WebEngine 用不起来的是**另一件事**：受限环境下 Chromium 建不出命名管道
（`named-platform-channel-pipe ... 拒绝访问 (0x5)`），它与显卡毫无关系。
两件事被写在同一个排障章节里，是这个问题反复消耗时间的根本原因。

---

## 2. 现象与原始证据（逐字保留，便于以后搜索比对）

### 2.1 GPU 报错（两条，总是一起出现）

```text
ERROR:gpu_channel_manager.cc(956) Failed to create GLES3 context, fallback to GLES2.
ERROR:gpu_channel_manager.cc(967) ContextResult::kFatalFailure: Failed to create shared context for virtualization.
```

实际抓到的完整行（含进程号/时间戳）：

```text
[3808:32232:1007/100220.788:ERROR:gpu_channel_manager.cc(956)] Failed to create GLES3 context, fallback to GLES2.
[3808:32232:1007/100220.788:ERROR:gpu_channel_manager.cc(967)] ContextResult::kFatalFailure: Failed to create shared context for virtualization.
```

### 2.2 命名管道报错（完全不同的另一件事）

```text
[23584:24896:1006/172755.160:FATAL:named-platform-channel-pipe(89)] Check failed: . : 拒绝访问。 (0x5)
```

本项目日志里真实出现过（`logs/zpymusic_20261006.log`）：

```text
17:27:57 INFO  zpymusic.sync.web_view  WebEngine 子进程探测失败：[23584:24896:1006/172755.160:FATAL:named-platform-channel-pipe(89)] Check failed: . : 拒绝访问。 (0x5)
17:30:06 DEBUG zpymusic.sync.web_view  WebEngine 后端就绪
17:30:06 INFO  zpymusic.ui.tab_play    曲谱视图后端：QWebEngineView（DOM 高亮）
17:30:07 DEBUG zpymusic.sync.local_server  HTTP "GET / HTTP/1.1" 200 -
```

**注意这 3 分钟的间隔**：17:27 探测失败、17:30 WebEngine 正常跑起来并成功取到页面。
同一台机器、同一份代码 —— 说明 17:27 那次失败是**运行方式**的差异，不是设备差异。

---

## 3. 曾经的错误结论（v1，已废弃）

旧 README / 旧代码注释里写的是：

> **含义**：Chromium 建不出 GLES3 上下文，退到 GLES2 也拿不到可共享的 GPU 上下文。
> **常见原因**：机器没有独显/核显驱动异常、远程桌面（RDP）会话、虚拟机、
> GPU 驱动被策略禁用、或显卡驱动版本过旧。
> **B. 已自动生效的默认修复**：程序启动时已自动设置软件渲染。

**为什么这个结论当时看起来很合理**（这是最值得记住的部分）：

1. 报错文本自带"权威感"：`GLES3`、`shared context`、`virtualization` 全是图形栈词汇；
2. 报错**在已经设了 `--disable-gpu` 的情况下依然出现**，看起来"连关掉 GPU 都不行"
   → 顺理成章推出"这台机器根本没有可用 GPU"。
   真相恰好相反：**是那个 flag 自己把 GLES3 上下文弄没的**。
3. 旧代码把"程序自动设置软件渲染"当成**修复**列在排障表里，于是"试过了、没用"
   → 进一步强化"机器有问题"的印象。（实际上 `QT_OPENGL=software` 单独设置确实无害，
   试它当然"没用"，因为它本来就不是病灶。）
4. 旧日志只打印 `QT_OPENGL`，**恰好漏掉了真正致错的 `QT_QUICK_BACKEND`**
   —— 按日志自查的人永远看不到关键变量。
5. GPU 报错与命名管道报错被写进同一节，读者会把两者的结论互相迁移。

### 3.1 为什么这个坑特别浪费时间

| 浪费点 | 具体表现 |
| :--- | :--- |
| **默认查不出来** | 后端默认是 `native`，平时根本不构造 Chromium，报错不出现 → 复现成本高、被降级为"偶发" |
| **归因方向错了就全白做** | 一旦认定是显卡/驱动/RDP，后续所有动作（更新驱动、换会话、加软件渲染参数）都注定无效 |
| **"修复"制造了病灶** | 为了让报错消失而加的软件渲染参数，正是报错的来源 → 越"修"越像机器坏了 |
| **探测给了假信心** | 旧探测把软件渲染参数一起塞进子进程，所以"探测通过"从来不等于"默认配置可用" |
| **排障工具本身是坏的** | `tools/diagnose_web_switch.py` 调用了早已改签名的 `_run_js(js, cb)` → 必然 `TypeError`；而且它从不 `show()` 视图，视口为 0，懒加载观察器不触发 → 输出"所有行都没加载"，指向完全错误的方向 |

---

## 4. 定位过程：最小对照实验

### 4.1 关键前提：先找一个"能工作的参照物"

`pyqt6-tutorial/pyqt6_demo/webview.py` 与 `browser.py` 在同一台机器、同一个解释器下
用 `QWebEngineView` 正常浏览网页，**从不报这两条错**。

它们与本项目的差异很小且可枚举：**只多设了 4 个路径类环境变量**
（`QTWEBENGINEPROCESS_PATH` / `QTWEBENGINE_RESOURCES_PATH` /
`QTWEBENGINE_LOCALES_PATH` / `QT_QPA_PLATFORM_PLUGIN_PATH`），
**完全没有设置任何 GL / GPU 相关变量**。

> **差异法**：同一台机、同一解释器、同一时刻，只保留"一个变量"的差异。
> 这比读一百条搜索结果都快。

### 4.2 逐项二分（这是决定性的一步）

不要一次加"一整套推荐配置"，而是**逐个 flag 单独加**，看哪一个是病灶。
可直接粘贴运行（普通桌面终端，**不要**在受限沙箱里跑，见 §4.4）：

```powershell
$py = "C:\miniconda3\envs\ibase\python.exe"

$probe = @'
import os, sys
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
from PySide6.QtWebEngineWidgets import QWebEngineView
v = QWebEngineView()
st = {}
def done(ok):
    st["ok"] = bool(ok); QTimer.singleShot(400, app.quit)
v.loadFinished.connect(done)
QTimer.singleShot(15000, app.quit)
v.setHtml("<html><body>ok</body></html>", QUrl("http://localhost/"))
v.resize(900, 600); v.show()
app.exec()
print("LOADED:", st.get("ok"))
'@

$names = @("QTWEBENGINE_CHROMIUM_FLAGS","QTWEBENGINE_DISABLE_SANDBOX","QT_OPENGL","QT_QUICK_BACKEND")
function Clear-GlEnv { foreach ($n in $names) { if (Test-Path "Env:$n") { Remove-Item "Env:$n" } } }

foreach ($flags in @("", "--disable-gpu", "--disable-gpu-compositing", `
                     "--disable-gpu-rasterization", "--disable-accelerated-2d-canvas", `
                     "--disable-dev-shm-usage", "--no-sandbox")) {
    Clear-GlEnv
    if ($flags) { $env:QTWEBENGINE_CHROMIUM_FLAGS = $flags }
    $out = $probe | & $py - 2>&1 | Out-String
    $gpu = if ($out -match 'gpu_channel_manager') { "<- GPU 报错" } else { "" }
    "{0,-30} {1} {2}" -f $(if ($flags) { $flags } else { "(只清空变量)" }), $gpu, $(if ($out -match 'LOADED: True') { "谱面正常" } else { "未加载" })
}

# 再单独验证 Qt 侧的两个变量
Clear-GlEnv; $env:QT_QUICK_BACKEND = "software"
$out = $probe | & $py - 2>&1 | Out-String
"QT_QUICK_BACKEND=software      " + $(if ($out -match 'gpu_channel_manager') { "<- GPU 报错" } else { "" })
Clear-GlEnv; $env:QT_OPENGL = "software"
$out = $probe | & $py - 2>&1 | Out-String
"QT_OPENGL=software             " + $(if ($out -match 'gpu_channel_manager') { "<- GPU 报错" } else { "" })
Clear-GlEnv
```

实测输出（PySide6 6.9.3 / Qt 6.9.3，Windows）：

| 变量 | GPU 报错 | 谱面 |
| :--- | :--- | :--- |
| **（只清空变量，什么都不设）** | 无 | 正常 |
| `--disable-gpu` | **有** | 正常 |
| `--disable-gpu-compositing` | 无 | 正常 |
| `--disable-gpu-rasterization` | 无 | 正常 |
| `--disable-accelerated-2d-canvas` | 无 | 正常 |
| `--disable-accelerated-video-decode` | 无 | 正常 |
| `--disable-dev-shm-usage` | 无 | 正常 |
| `--no-sandbox` | 无 | 正常 |
| `QTWEBENGINE_DISABLE_SANDBOX=1` | 无 | 正常 |
| **`QT_QUICK_BACKEND=software`** | **有** | 正常 |
| `QT_OPENGL=software` | 无 | 正常 |

**结论只有两个病灶**：`--disable-gpu` 与 `QT_QUICK_BACKEND=software`。
而这两样，恰好都是本项目自己在 `prepare_webengine_env()` 里设的。

补充证据：本机 `QOpenGLContext` 能建出 **OpenGL 4.6 Compatibility** 上下文
（`create=True, isValid=True`）—— 硬件与驱动确实正常。

### 4.3 用"真实业务内容"再确认一次

用最小的假页面还不够，要用**真实曲谱页**验证"干净环境能不能干活"：
起 `LocalAssetServer` 指向真实套件，让 Chromium 加载 6 张 `sys-*.svg`。

结果：**无任何 GPU 报错，6/6 张 SVG 渲染成功**（`naturalWidth > 0`）。

### 4.4 一个必须知道的实验限制

如果实验跑在**受限沙箱 / 受管环境**里（例如由自动化代理会话启动），
Chromium 会在更早的一步就 `FATAL:named-platform-channel-pipe ... 拒绝访问 (0x5)` **直接 abort**，
`--disable-gpu` 的效应还没机会显现。

**同一条探针命令，两种运行方式的差别**（本案例实测）：

| 运行方式 | 结果 |
| :--- | :--- |
| 受限沙箱模式 | `FATAL:named-platform-channel-pipe ... 拒绝访问 (0x5)`，退出码 `-2147483645` |
| 非受限（普通终端 / IDE） | 一切正常，无报错，页面照常渲染 |

所以：**做这类二分实验务必在普通桌面终端里跑**，否则会得到"WebEngine 完全不可用"的假象，
并把结论错误地套到 GPU 上。

---

## 5. 真正的原因（两条独立故障，之前被混为一谈）

### 故障 A：自伤 —— 自己关掉 GPU

Chromium 的 GPU 进程在"被要求走软件路径"时，无法建立起 GLES3 上下文，
于是打印 §2.1 那两条。触发它的正是我们注入的：

* `--disable-gpu`（`QTWEBENGINE_CHROMIUM_FLAGS` 内）；
* `QT_QUICK_BACKEND=software`（让 Qt Quick 走软件后端，QtWebEngine 的合成器因此
  拿不到可共享的 GL 上下文 —— 与报错里的 `shared context for virtualization` 完全对应）。

**性质：非致命**。Chromium 退回软件合成，页面照样显示 —— 所以它只是"难看的日志"，
但代价是**真的失去了 GPU 加速**，而且把排障方向带偏了。

### 故障 B：环境 —— 命名管道被拒（与 GPU 无关）

Chromium 用命名管道做多进程 IPC。在受限沙箱 / 受管环境里创建被拒 →
`FATAL ... (0x5)` → **致命 abort**，Python 侧无法 try/except。
这解释了"日志说 WebEngine 不可用，但在自己终端里又是好的"。

### 还有一个隐藏代价：环境变量污染子进程

`prepare_webengine_env()` 过去在 `run_gui()` 里、`QApplication` 之前**无条件**执行，
用的是 `os.environ.setdefault`。于是这些 Qt/GL 变量会随环境**继承给所有子进程**。
同源事故已经发生过一次：MuseScore 4 继承 `QT_QPA_PLATFORM=offscreen` 直接崩溃
（`0xC0000409`，见 `docs/requirements.md` §12.10）。

> **规律**：在主进程里 `setdefault` 一份"给某个组件用的环境变量"，
> 等于给整个进程树下了全局副作用。能推迟就推迟，能只给子进程就只给子进程。

---

## 6. 修复内容与现在的行为约定

| 位置 | 现在的约定 |
| :--- | :--- |
| `_DEFAULT_FLAGS` | **只有** `--disable-dev-shm-usage`（Windows 上是空操作）。不得出现任何 `--disable-gpu*`、`--no-sandbox` |
| `_SOFTWARE_FLAGS` | 关 GPU 的那批 flag **只在这里**，作为兜底。**不得**包含 `--disable-software-rasterizer`（它会连软件路径一起禁掉） |
| `_SOFTWARE_QT_ENV` | `QT_OPENGL=software` + `QT_QUICK_BACKEND=software`，**只在兜底 profile 生效**；绝不可无条件设置 |
| `is_webengine_available()` | **两段式**：① 先用默认（GPU）配置探测；② 只有失败原因是 `REASON_GL` 时才用软件渲染重试。管道/超时**不重试** |
| `classify_probe_failure()` | 把失败分成 `pipe` / `gl` / `timeout` / `other`，并按类给提示 —— 不允许再把一切都说成"沙箱" |
| `active_profile()` | 任何时刻都能查到当前生效的是 `default` 还是 `software` |
| `run_gui()` | **不**注入任何 Qt/GL 变量；需要时由 `WebScoreView.__init__` 按 profile 注入 |
| 日志 | 每次启动打印 `WebEngine 环境：profile=… | QTWEBENGINE_CHROMIUM_FLAGS=… | QT_OPENGL=… | QT_QUICK_BACKEND=…`（补齐了关键变量） |
| 默认后端 | `UiConfig.score_backend = "auto"`（浏览器引擎优先、失败回退原生） |

### 6.1 如果以后又要给 WebEngine 加启动参数

按这个顺序做，别跳步：

1. 改之前先跑一次基线：`& $py tools\probes\probe_webengine_perf.py --compare --rows 60`；
2. 加参数后**再跑一次**，确认默认 profile 的「GPU 报错」列仍是 **无**；
3. 确认 `mounted == rows` 且 `err == 0`（参数不能把功能弄坏）；
4. 如果新参数会让**默认** profile 报错 → 它属于 `_SOFTWARE_FLAGS`，**不属于** `_DEFAULT_FLAGS`；
5. 跑 `pytest -q`，`tests/test_webengine_env.py` 的 17 项会挡住回退。

---

## 7. 验收数据（本次修复后实测，可复现）

`the-four-seasons-complete`，60 行，1200×900：

| profile | page_ms | first_ms | scroll_ms | mounted | svg | err | GPU 报错 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| `default`（GPU） | 180.0 | 66.2 | 22376.7 | 60/60 | 60 | 0 | **无** |
| `software`（兜底，对照组） | 150.5 | 66.5 | 21945.1 | 60/60 | 60 | 0 | 有（符合预期） |

其它验证：

* `tools/diagnose_web_switch.py`：`is_webengine_available()=True`、`profile=default`、
  套件 A→B→A 连续切换 **0 行加载失败**；
* `tools/check_gui_launch.py`：曲谱视图后端 = `WebScoreView`，stderr **无任何 `gpu_channel_manager` 报错**；
* `pytest -q`：**338 项全部通过**（含本次新增 17 项；补齐缩放接口后为 **364 项**，见 §9.1）。

> ⚠ **一个必须诚实记录的负面结果**：两个 profile 的 `scroll_ms` **基本一致**（比值 ≈ 1.0）。
> 这条路径的耗时由"取回 + 解析 + 栅格化 60 张 SVG"主导，都在 CPU 侧。
> 所以本次修复的意义**不是"GPU 更快"**，而是：不再发误导性报错、不再无谓关闭 GPU 合成、
> 软件渲染只在真正需要时启用、不再污染子进程环境。
> （早期一次测量得到"软件渲染慢 1.17×"，那是轮询节拍噪声，已作废 —— 记录在此以免被再次引用。）

---

## 8. 通用排查清单（下次遇到"环境变量 / 渲染后端 / 子进程"类问题照做）

### 第一步：分类，别急着动手

1. **致命还是非致命？** 看进程是否继续、功能是否可用。
   非致命 = 日志噪音问题（优先级降低，但它可能正在掩盖真问题，别删掉不管）。
2. **设备能力还是环境限制？** 同一台机器上找一个能工作的参照物。
3. **是一次性还是稳定复现？** 稳定复现才好二分；偶发先想办法把它变成稳定复现。

### 第二步：差异法（最强的一招）

找一个**能工作的同类程序**，把差异压到"同一台机、同一解释器、同一库版本、同一时刻，
只差一个变量"。不要凭报错文本猜。

### 第三步：二分变量

* 环境变量、启动参数**逐个**加/减，一次只动一个；
* 不要一次套用"网上推荐的一整套配置"—— 一套里只要有一个病灶，整组都会被误判；
* 记录成「变量 → 结果」表，而不是只记结论。

### 第四步：问"这个变量是谁设的？"

```powershell
# 找出代码里所有自己设环境变量的地方
Select-String -Path src\zpymusic\**\*.py -Pattern "setdefault|os\.environ\[|putenv"
```

**特别小心 `setdefault`**：它会让人误以为"值来自系统"，其实是代码给的默认值；
"我没设啊"和"代码替我设了"在现象上无法区分。

### 第五步：别相信"常见原因"清单

报错文本里的关键词（GPU / 虚拟化 / 驱动）会强烈诱导归因。
**先做一次"清空所有相关变量"的基线实验**，成本 30 秒，能省掉几小时。

### 第六步：区分"探测通过"与"默认可用"

* 探测脚本必须使用**用户真实运行时的那套配置**；
* 若探测自带"修复参数"，它的结论**不可迁移**到默认路径；
* 探测要记录"当前生效的是哪一套配置"（本项目的 `active_profile()`）；
* 失败要**分类**：不同原因处置方式完全不同（管道 = 换环境；GL = 换渲染路径）。

### 第七步：环境变量会污染整个进程树

在主进程里设置的 Qt / OpenGL / Chromium / 语言 / 代理类变量，会被**所有子进程**继承。
MuseScore、ffmpeg、FluidSynth 都可能因此行为异常或崩溃。
**能推迟到"真正需要的那一刻"就推迟；能只给某个子进程就只给那个子进程。**

### 第八步：先确认排障工具本身是对的

排障脚本自己坏掉会产出**看起来很确定**的假结论（本案例里工具既调错了 API、
又没把视图显示出来，导致"所有行都加载失败"）。
**做法**：先让工具跑出一个你已经知道答案的用例，答案对上了再信它。

### 第九步：留下证据矩阵，别只留结论

结论会过期（依赖版本一变就可能反转），证据不会。
把「实验 / 环境 / 结果」三列表写进仓库，比在聊天记录里写"已确认是显卡问题"有用一百倍。

### 第十步：修完要有二元判定 + 回归测试

优先选可 PASS/FAIL 的断言，而不是"体感变快了"：

* ✅「默认 profile 不得出现 `gpu_channel_manager` 报错」（Grep stderr 即可判定）
* ✅「`mounted == rows` 且 `err == 0`」
* ❌「感觉流畅一些了」

---

## 9. 关键词 → 可能原因 → 第一步验证（速查）

| 报错关键词 | 最可能的原因 | 第一步验证 |
| :--- | :--- | :--- |
| `Failed to create GLES3 context, fallback to GLES2` | 有人把 GPU 关掉了：`--disable-gpu` 或 `QT_QUICK_BACKEND=software` 或系统级软件 GL 设置 | **清空这些变量**重跑 |
| `ContextResult::kFatalFailure: Failed to create shared context for virtualization` | 同上（Qt Quick 后端被强制成非默认值，QtWebEngine 合成器拿不到可共享 GL 上下文） | 同上；尤其检查 `QT_QUICK_BACKEND` |
| `FATAL:named-platform-channel-pipe ... 拒绝访问 (0x5)` | 运行环境禁止创建命名管道（受限沙箱 / 容器 / 受管环境） | **换普通终端跑**；与显卡无关 |
| 加了 `--disable-software-rasterizer` 后彻底建不出上下文 | 该 flag 连软件路径一起禁掉 | 删掉它（本项目曾踩过，已移除） |
| `QOpenGLContext::create()` 返回 False | 真的没有可用 GL | 试 `QT_OPENGL=software`；确认 `opengl32sw.dll` 存在 |
| 子进程（MuseScore/ffmpeg 等）莫名崩溃 | 继承了主进程的 Qt 环境变量 | 启动子进程前 `pop` 掉 `QT_QPA_PLATFORM` 等 |

---

## 9.1 姊妹案例：换默认实现 = 换 bug 面（"静默降级"类）

同一个功能有**多个实现**、而调用方用 `hasattr` / `getattr(..., 默认值)` 探测能力时，
少一个方法**不会报错**，只会"安静地什么都不做"。这类问题的表现和"功能本来就写坏了"
一模一样，排查时很容易往算法里找，其实缺的是方法。

**本项目真实发生（M6，就在 GPU 那次修完之后）**：把默认后端从 `native` 改成 `auto`，
用户立刻反馈「同步播放」页的**「适应宽度」失效、永远 100%**。真相是：

* `ui/score_host.py` 用 `hasattr(score_view, "set_fit_width")` 判断支不支持，缺了就 `return`；
* `WebScoreView` 只有 `set_zoom` / `zoom`，没有 `set_fit_width` / `fit_width_enabled` /
  `zoom_changed`；
* 而按钮初始勾选状态取自 `getattr(score_view, "_fit_width", True)` ——
  **默认值是 `True`**，于是按钮显示"已勾选"，用户以为开关是开的。

**可迁移的教训**：

1. **换默认实现时，先做"接口对照"**：把两个实现的公开方法逐名列出来 diff，
   而不是等用户报障。一条 `for name in REQUIRED_API: assert hasattr(cls, name)`
   就能永久拦住这类回归。
2. **`getattr(obj, "flag", True)` 这种写法很危险**：属性缺失时会"乐观地"假定功能可用，
   把一个"缺失"变成"看起来正常"。要么显式检查，要么让默认值表示"不可用"。
3. **`hasattr` 静默降级要留下痕迹**：降级路径至少 `log.warning` 一次，
   否则排障时根本不知道走的是哪条分支。
4. 差一截的另一种表现：`align-items: center` 在内容溢出时会把两端都裁掉
   （本案例里浏览器侧"谱面比视口宽时左边缘滚不回来"就是这个）。
   **凡是"居中 + 可能溢出"的布局，都要用 `safe center` 或显式处理溢出。**

---

## 10. 复现与自检入口

```powershell
$py = "C:\miniconda3\envs\ibase\python.exe"
Set-Location "C:\MyCodes\py-space\zpymusic"

# 1) 二元判定：默认(GPU) 必须无 GPU 报错；software 对照组必须有（证明因果）
& $py tools\probes\probe_webengine_perf.py --compare --rows 60

# 2) 端到端：探测结论 + 失败分类 + 多套件切换逐行状态
& $py tools\diagnose_web_switch.py

# 3) GUI 真实启动链（开窗 2 秒自退）
$env:QT_QPA_PLATFORM = "offscreen"; & $py tools\check_gui_launch.py

# 4) 回归测试（守住"默认不关 GPU / 不设软件渲染 / 不注入 no-sandbox"）
& $py -m pytest -q
```

---

## 11. 时间线摘要（供快速了解经过）

| 时点 | 事件 |
| :--- | :--- |
| M4 期间 | WebEngine 后端首次落地；启动时注入一整套"软件渲染"参数；随后出现两条 GPU 报错 |
| 同上 | 报错被归因于"机器没有 GPU / 驱动异常 / RDP / 虚拟机"，并写进 README 排障章节 |
| 同上 | 同时遇到命名管道 `FATAL ... (0x5)`；两个问题被写进同一节，结论互相污染 |
| 同上 | 默认后端改为 `native` 绕开问题 —— 报错消失，但**根因被掩盖**，且 GPU 加速被永久放弃 |
| M6 | 用户报告："本机 GPU/驱动都正常，其他项目的 `QWebEngineView` 从不报错" |
| M6 | 差异法 + 逐变量二分 → 锁定 `--disable-gpu` 与 `QT_QUICK_BACKEND=software` |
| M6 | 真实曲谱页干净环境验证：无报错、6/6 渲染成功；Qt 可建 OpenGL 4.6 上下文 |
| M6 | 重写环境策略（默认不关 GPU + 两段式探测 + 失败分类）、修复坏掉的排障工具、默认后端改 `auto` |
| M6 | 新增 17 项回归测试与对比基准；338 项测试全绿；文档按实测改写 |
| M6（随后） | 默认后端改 `auto` 后**立刻**暴露「适应宽度」失效 —— 实为两个后端接口不一致导致的静默降级（§9.1），另修掉页面居中布局"左边缘滚不回来"。新增 25+1 项回归测试，总计 **364 项全绿**（受限沙箱与可启动 Chromium 两种环境下各跑一遍都通过） |
