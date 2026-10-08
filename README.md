# zpyMusic

以 **MusicXML 为权威数据源**的曲谱工具：生成 `svg / midi / mp3 / sync.json` 套件，并在播放时于
SVG 曲谱上**同步高亮当前音符**。

A sheet-music tool that takes **MusicXML as the authoritative data source**: it generates an
`svg / midi / mp3 / sync.json` suite and **highlights the current note in sync** on the SVG score
during playback.

需求说明书见 [`docs/requirements.md`](docs/requirements.md)（v2.1，含 M0–M2 实测回填）。

---

## 当前进度

| 里程碑 | 内容 | 状态 |
| :--- | :--- | :--- |
| **M0** | 环境校验、配置、日志、主窗口 + 三标签 + 日志 Dock + 设置 | ✅ 完成 |
| **M1** | 核心流水线 `MusicXML → SVG / MIDI / MP3 / sync.json` + CLI | ✅ 完成 |
| **M2** | 「生成套件」界面（批量、进度、取消、覆盖策略、音色映射表） | ✅ 完成 |
| **M3** | 音频链路（FluidSynth → WAV → MP3） | ✅ 已在 M1 完成 |
| **M4** | **「同步播放」界面**：SVG 曲谱 + 同步高亮 + 传输控制 + 速率/校准 | ✅ 完成 |
| M5 | 格式转换界面与其余格式（PDF / midi→musicxml / svg→musicxml …） | ⏳ 待做 |
| M6 | 打磨、打包 | ⏳ 待做 |

**已完成的实测结果**

| 项目 | 结果 |
| :--- | :--- |
| 10 个样本生成套件 | 全部成功 |
| `sync.json` 音符 ID ↔ SVG 一致性 | **43281 个事件 100% 命中** |
| MIDI 与时间轴同源校验 | 误差 2–16 ms |
| 高亮起点 ↔ MIDI note-on 对齐 | **10/10 套件 100% 命中**（±100 ms） |
| 单元测试 | **370 项全部通过** |
| M4 播放冒烟测试 | 28 项检查 × 3 个套件（含 276 行的 four-seasons）全部通过 |
| 懒加载性能（four-seasons 276 行） | 载入 79 ms；单行挂载 9–22 ms；滚动挂载 60 行 1.17 s |

---

## 运行环境

已在本机验证（**当前环境：Python 3.14.7 / PySide6 6.12.0**，2026-10-08 升级后已全量复验，
见下文「环境升级复验记录」）：

| 项 | 值 |
| :--- | :--- |
| Python | **3.14.7** @ `C:\miniconda3\envs\ibase\python.exe`（Anaconda，`MSC v.1942 64 bit (AMD64)`；`C:\miniconda3\envs` 下唯一环境 `ibase`） |
| GUI 框架 | **PySide6 6.12.0**（`PySide6_Essentials` / `PySide6_Addons` 6.12.0、`PySide6_WebEngine` 6.12.0.140、`PySide6_Pdf` 6.12.0.140、`shiboken6 6.12.0`）→ **Qt 6.12.0** |
| 依赖 | `verovio 6.3.0`、`music21 10.5.0`、`pyfluidsynth 1.4.0`、`mido 1.3.3`、`numpy 2.5.3`、`scipy 1.18.1` |
| 测试工具 | `pytest 9.1.1`、`pytest-qt 4.5.0`、`setuptools 84.0.0` |
| FluidSynth | `tools/fluidsynth/`（`fluidsynth.exe`、`libfluidsynth-3.dll`、`SDL3.dll`、`sndfile.dll`，2.6.1） |
| ffmpeg | `C:\ffmpeg\bin\ffmpeg.exe`（`N-122544-g8966101fa6-20260125`，含 `libmp3lame`） |
| MuseScore | `C:\Program Files\MuseScore 4\bin\MuseScore4.exe`（PDF 导出用，M5） |
| 音色库 | `sound/`（5 个 `.sf2/.sf3`；默认 `MuseScore_General.sf3`，MIT） |

> **Python 3.14 说明**：`pyproject.toml` 的 `requires-python = ">=3.11"` 已覆盖 3.14，
> 无需改动；`verovio 6.3.0` 是 `cp310-abi3` 轮子，在 3.14 上照常加载。
> **注意 `python` 命令**：PATH 上的 `python.exe` 是 Windows 商店占位程序（`WindowsApps`），
> 直接敲 `python` 会静默失败（退出码 1、无输出）。**必须用绝对路径**调用 `ibase` 解释器。

### 环境升级复验记录（2026-10-08，3.13.16/6.9.3 → 3.14.7/6.12.0）

| 检查 | 命令 | 结果 |
| :--- | :--- | :--- |
| 依赖自检 | `python -m zpymusic probe` | **6/6 全部可用**（verovio 6.3.0 / fluidsynth 2.6.1 / ffmpeg / MuseScore 4 / music21 10.5.0 / 5 个音色库） |
| 套件交叉验收 | `python tools/verify_suites.py` | **10/10 套件、43281 个事件 100% 命中**；ID 一致 / MIDI 同源（2–16 ms）/ 音频未截断 / note-on 对齐全通过 |
| M4 播放冒烟 | `python tools/smoke_play.py` | `canon-in-d-easy` 与 `the-four-seasons-complete` **各 28/28 通过** |
| GUI 启动 | `python tools/check_gui_launch.py` | PASSED（三标签页、日志 Dock、曲谱视图后端、图标均正常） |
| GUI 自检 | `python src/zpymusic/gui.py --selftest` | PASSED（真实 `windows` 平台，UI 导入链 OK） |
| 端到端生成 | `python -m zpymusic generate <源> -o <绝对路径>` | 成功：SVG / MIDI / `sync.json` / MP3 齐全，音频 **27.6× 实时** |
| 套件校验 | `python -m zpymusic validate <套件根>` | OK |
| 单元测试 | `python -m pytest -q` | **370 项全部通过**（0 失败 / 0 跳过，27 s） |

**升级后处置**（下面两项均已确认并处理完毕）

1. **`tests/test_m4_playback.py` 的反向护栏测试已按 Qt 版本更新**（原先在新环境下失败）。
   它断言"不内联 `stroke` 时谱线必须看不见"，用来反证 `flatten_svg(inline_stroke=True)`
   这个修补是必要的。实测 **Qt 6.12.0 的 QtSvg 已支持内嵌 CSS 规则并解析 `currentColor`**，
   该前提不再成立（对照实验：`sys-0001.svg` 内联 211651 深色像素 / 不内联 211204，
   横贯谱线都是 60 行；最小复现中 `#id path{stroke:currentColor}` 生效、
   把选择器改成不匹配的 `#nope` 则完全不画 —— 证明它真的在做选择器匹配）。
   现在该测试**按 Qt 版本分两路断言**（`< 6.12` 断言不可见、`>= 6.12` 断言可见），
   两路都在断言可观测事实，任一侧翻转即失败。**程序渲染功能本身一直是正常的**，
   且 `inline_stroke_attributes()` 现为**冗余但无害**的兼容保险（保留以支持 Qt < 6.12）。
   详见下方「坑 10」。
2. **`-o/--suites-dir` 传相对路径时音频渲染会失败**（`fluidsynth: fluid_is_soundfont(): fopen() failed`）。
   原因是 `core/audio_render.py` 为了找同级 DLL 把子进程 `cwd` 设成了 `tools/fluidsynth/`，
   于是相对路径的 `.mid` 被解析到了那个目录下。**传绝对路径即正常**（本文档所有命令都用绝对路径）。
   该问题与 Python / Qt 版本无关，是既有的路径处理缺陷，仅在使用相对输出目录时触发
   —— **尚未修改**，如需修复请另行确认。

首次使用先自检：

```powershell
$env:PYTHONPATH = "C:\MyCodes\py-space\zpymusic\src"
& C:\miniconda3\envs\ibase\python.exe -m zpymusic probe
```

可选：安装为可编辑包，之后可直接写 `zpymusic` / `zpygui`：

```powershell
& C:\miniconda3\envs\ibase\python.exe -m pip install -e "C:\MyCodes\py-space\zpymusic"
```

---

## 快速开始

### 图形界面

```powershell
# 方式 A（推荐）：按模块启动
$env:PYTHONPATH = "C:\MyCodes\py-space\zpymusic\src"
& C:\miniconda3\envs\ibase\python.exe -m zpymusic.gui

# 方式 B：直接执行文件（IDE 里点“运行”的默认做法，同样可用）
& C:\miniconda3\envs\ibase\python.exe "C:\MyCodes\py-space\zpymusic\src\zpymusic\gui.py"

# 方式 C：先安装为可编辑包，然后直接用命令
& C:\miniconda3\envs\ibase\python.exe -m pip install -e "C:\MyCodes\py-space\zpymusic"
zpygui
```

> **启动排障**：如果看到
> `ImportError: attempted relative import with no known parent package`，
> 说明是在用旧代码按文件路径执行。现在 `gui.py` / `__main__.py` 已用
> `__package__` 守卫 + 绝对导入兼容两种方式；如仍报错，先跑
> `python tools/diagnose_launch.py` 自检。
> 想在不弹窗的情况下确认启动链是否正常，加 `--selftest`：
> `python src/zpymusic/gui.py --selftest`。

### 三个标签页

1. **生成套件** —— 选源文件 → 确认/修改音色映射（F1.8）→ 选音色库与产物 → 开始生成。
   进度、警告、日志都在底部日志区；支持批量与取消。
2. **同步播放** —— 左侧选套件，右侧显示 SVG 曲谱；播放时**同步高亮当前音符**，
   支持拖动进度条（拖动时实时预览高亮）、点击谱面跳转到该音符、速率 50%–200%、
   高亮颜色/透明度、自动跟随滚动、`offset_ms` 校准。
   曲谱视图左上角有**缩放工具条**（`−` / `+` / 百分比 / **适应宽度**，范围 30%–400%）——
   默认勾选"适应宽度"，谱面自动铺满窗口，不会出现横向滚动条与两侧留白。
3. **格式转换** —— 已提供按源格式动态过滤的能力矩阵与保真度提示；执行在 M5 接入。

**播放页快捷键**：`空格` 播放/暂停 · `←/→` 快退/快进 1s · `Ctrl+←/→` 上/下一个音符 ·
`</>` 变速 · `Ctrl+0` 回 100% · `Home` 回到开头 ·
`Ctrl+=` / `Ctrl+-` 缩放 · `Ctrl+W` 切换"适应宽度"。

> **关于曲谱视图后端**：默认 `auto` —— 优先 `QWebEngineView`（DOM 高亮，缩放/滚动/命中
> 测试都由浏览器负责，性能最好），不可用时自动回退到 **QtSvg 原生后端**
> （自绘半透明高亮框），功能等价、永不失败。可用环境变量 `ZPYMUSIC_WEBENGINE=1/0`
> 强制指定，也可在「设置 → 界面 → 曲谱渲染后端」里锁定 `native` / `web`。
>
> 可用性由**子进程探测**决定，且是**两段式**：先用**默认（GPU 加速）配置**试一次，
> 只有失败原因确实是「建不出 GL 上下文」时，才降级到软件渲染兜底再试一次。
> 受限沙箱（如 CI 或某些受管环境）下 Chromium 会因命名管道被拒而无法启动 ——
> 这与显卡无关，此时就会走原生后端。

### 排障：浏览器引擎的 GPU 报错

> 完整的"前因后果 + 通用排查方法论"见
> [`docs/troubleshooting-webengine-gpu.md`](docs/troubleshooting-webengine-gpu.md)
> —— 这个坑在本项目里反复消耗过大量时间，那份文档记录了当时错在哪、以及下次怎么用 30 秒定位。
> 下面是面向使用者的简版。

如果日志里出现下面这类报错：

```
ERROR:gpu_channel_manager.cc(956) Failed to create GLES3 context, fallback to GLES2.
ERROR:gpu_channel_manager.cc(967) ContextResult::kFatalFailure:
                                  Failed to create shared context for virtualization.
```

**结论（M6 实测更正）**：这两条**恰恰是程序自己关掉 GPU 造成的**，不是机器的问题。
旧版本在启动时无条件注入 `--disable-gpu` 与 `QT_QUICK_BACKEND=software`，报错就是它们
发出来的。现在默认不再注入，报错随之消失。

同一台机器、同一解释器的对照实验（**当时的** PySide6 6.9.3 / Qt 6.9.3；当前环境已升级为
6.12.0，下表为历史实测记录，加载同一套件前 6 行 SVG）：

| 实验 | 环境 | GPU 报错 | 谱面 |
| :--- | :--- | :--- | :--- |
| 基线 | 不设任何 GL / WebEngine 变量（等同 `pyqt6-tutorial` 的 `QWebEngineView` demo） | 无 | 正常 |
| 旧默认 | 全套注入（含 `--disable-gpu`、`QT_QUICK_BACKEND=software`） | **有** | 正常（非致命） |
| 单项 | 只加 `QT_QUICK_BACKEND=software` | **有** | 正常 |
| 单项 | 只加 `QT_OPENGL=software` | 无 | 正常 |
| 单项 | 只加 `--disable-gpu` | **有** | 正常 |
| 单项 | 只加 `--disable-dev-shm-usage` / `--no-sandbox` / 其余 `--disable-*` | 无 | 正常 |
| 真实曲谱页 | 干净环境 | 无 | **6/6 张 SVG 渲染成功** |

所以：

* **报错本身确实非致命** —— 上表每一行谱面都正常显示；
* 但"常见原因"（没有独显 / 驱动异常 / 远程桌面 / 虚拟机 / 驱动被策略禁用）**都不成立**：
  本机 Qt 能建出 **OpenGL 4.6** 上下文，`pyqt6-tutorial` 里同样用 `QWebEngineView` 也从不报错；
* 代价却是真实的：`--disable-gpu` 会连带关掉 GPU 合成 / 光栅化。

**现在怎么处理**

| 做法 | 操作 |
| :--- | :--- |
| **A. 什么都不用做（推荐）** | 默认已不注入这些标志。日志里 `WebEngine 环境：profile=default …` 就表示走的是 GPU 路径 |
| B. 想彻底绕开浏览器引擎 | 「设置 → 界面 → 曲谱渲染后端」选 **原生 QtSvg**，重启程序 |
| C. 机器真的没有可用 GPU | 不用手动设置：探测会先试默认配置，失败且原因是 GL 时**自动**降级软件渲染（日志里 `profile=software`） |
| D. 想强制软件渲染 | 启动前设 `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`（会重新出现上面两条报错，属预期） |

> 日志里这一行会打印实际生效的 profile 与三个关键变量：
> `WebEngine 环境：profile=… | QTWEBENGINE_CHROMIUM_FLAGS=… | QT_OPENGL=… | QT_QUICK_BACKEND=…`
> （旧版本只打印 `QT_OPENGL`，恰好漏掉了真正致错的 `QT_QUICK_BACKEND`。）
>
> **另一个容易与它混淆的报错**：`FATAL:named-platform-channel-pipe ... 拒绝访问 (0x5)`。
> 它与 GPU 无关，是运行环境不允许创建命名管道（受限沙箱 / 受管环境，例如由自动化代理
> 会话启动）造成的，Chromium 会直接 abort。普通桌面下不会出现，程序会自动回退原生后端；
> `tools/diagnose_web_switch.py` 会把失败原因分类打印出来（管道 / GL / 超时 / 其它）。

**已不再注入的变量**（旧版本会，现已移除）：

```text
--disable-gpu --disable-gpu-compositing --disable-gpu-rasterization
--disable-accelerated-2d-canvas --disable-accelerated-video-decode
--no-sandbox
QTWEBENGINE_DISABLE_SANDBOX=1
QT_OPENGL=software
QT_QUICK_BACKEND=software
```

> 现在只剩默认 profile 的 `--disable-dev-shm-usage`（Windows 上是空操作），
> 软件渲染兜底 profile 才追加那批 `--disable-gpu*`。
>
> **刻意不加** `--disable-software-rasterizer`：它连软件路径一起禁掉，
> 在"没有可用 GPU"的机器上会让上下文创建彻底失败。旧版本曾带这个标志，已移除。
>
> `--no-sandbox` / `QTWEBENGINE_DISABLE_SANDBOX` 也一并移除，恢复 Chromium 默认沙箱：
> 实测本机不需要它们，而在真正需要它们的受限环境里，Chromium 早就死在命名管道上了。
>
> 另外，这些变量以前是在 `run_gui()` 里**无条件**设置的，会随 `os.environ` 泄漏给
> **所有子进程**（MuseScore 继承 `QT_QPA_PLATFORM` 崩溃就是同类问题的另一例，
> 见需求 §12.10）。现在只在真正要构造 `QWebEngineView` 时按探测出的 profile 注入。

### 命令行

```powershell
$env:PYTHONPATH = "C:\MyCodes\py-space\zpymusic\src"
$py = "C:\miniconda3\envs\ibase\python.exe"

& $py -m zpymusic probe                      # 环境自检
& $py -m zpymusic inspect <文件|套件目录>     # 查看声部 / MIDI 通道 / GM 音色 / 套件规模
& $py -m zpymusic generate <源...> [选项]     # 生成套件
& $py -m zpymusic validate <套件根目录>       # 校验套件完整性
& $py -m zpymusic list                       # 列出已有套件
& $py -m zpymusic conversions                # 打印格式转换能力矩阵
```

`generate` 常用选项：`-o/--suites-dir`、`-s/--soundfont`、
`--overwrite={skip,overwrite,newdir}`、`--keep-wav`、`--no-svg`、`--no-midi`、`--no-audio`、
`--json`、`-q`。

批量生成全部样本：

```powershell
& $py -m zpymusic generate "C:\MyCodes\py-space\zpymusic\staff\musicxml" --overwrite=overwrite -q
& $py -m zpymusic validate "C:\MyCodes\py-space\zpymusic\staff\suites"
```

---

## 套件目录结构

```
staff/suites/<主名>/
    <主名>.musicxml      规范化后的 MusicXML（套件内的权威源）
    <主名>.mxl           原始压缩源（仅溯源用）
    <主名>.mid           Verovio 导出（与时间轴同源）
    <主名>.mp3           FluidSynth 渲染 + ffmpeg 编码
    <主名>.wav           可选中间产物（默认生成后删除）
    <主名>.sync.json     时间轴 + 元数据（播放界面唯一依赖）
    svg/sys-0001.svg …   每个 system（一行谱）一个 SVG
```

`sync.json` 的完整契约见 [`docs/requirements.md`](docs/requirements.md) §5.1.4。

---

## 项目结构

```
zpymusic/
  src/zpymusic/
    common/     config.py  paths.py  log.py  deps.py  errors.py
    core/       musicxml_io.py  score_render.py  sync_model.py  suite.py
                audio_render.py  midi_tools.py  gm.py  pipeline.py
    sync/       timeline.py       # 时间轴与增量高亮（无 Qt）
                svg_geometry.py   # 解析 SVG 元素几何（高亮框 / 点击命中）
                highlight.py      # 高亮计划 + 注入 CSS
                score_view.py     # ScoreView 抽象 + QtSvg 原生后端
                web_view.py       # QWebEngineView 后端（DOM 高亮）
                local_server.py   # 只读本地 HTTP 服务（给 WebEngine 供图/供音频）
                player.py         # PlaybackBackend + QMediaPlayer
                controller.py     # 播放编排（30 fps tick / 变速 / 校准）
    ui/         main_window.py  log_dock.py  settings_dialog.py  worker.py  job.py
                tab_generate.py  tab_play.py  tab_convert.py  parts_table.py
    cli.py  gui.py  __main__.py
  tests/        conftest.py  test_timeline.py  test_sync_model.py  test_svg_geometry.py
  tools/
    fluidsynth/                          # FluidSynth 2.6.1 运行时
    probes/                              # 实测探针（可复现需求文档附录 D 的数据）
    verify_suites.py                     # 10 套件交叉验收
    smoke_play.py                        # M4 播放功能冒烟测试
    check_gui_launch.py                  # GUI 启动检查（开窗 2 秒后自动退出）
    diagnose_launch.py                   # 启动方式诊断（文件执行 vs -m）
    diagnose_stafflines.py               # 谱线不显示的原因验证
    diagnose_highlight.py                # 真实播放时高亮/跟随诊断
    patch_requirements_m4.py             # 文档回填脚本（一次性）
  docs/         requirements.md  predo.md  troubleshooting-webengine-gpu.md
  sound/  staff/  logs/
```

**分层规则**：`core/` 与 `sync/` **不导入 PySide6**，因此 CLI 与单测都能脱离 GUI 运行；
`ui/` 只调用它们的公开接口。

---

## 测试

```powershell
Set-Location "C:\MyCodes\py-space\zpymusic"
$py = "C:\miniconda3\envs\ibase\python.exe"

# 单元测试（370 项：时间轴/高亮、SVG 几何与谱线渲染、播放状态机、sync.json 契约、MusicXML 解析、GM 音色）
& $py -m pytest -q

# 套件交叉验收（10 个套件：ID 一致性 / MIDI 同源 / 音频未截断 / 高亮起点对齐）
& $py tools\verify_suites.py

# M4 播放功能冒烟（28 项检查；可指定套件名，默认 canon）
& $py tools\smoke_play.py canon-in-d-easy
$env:QT_QPA_PLATFORM = "offscreen"; & $py tools\smoke_play.py the-four-seasons-complete

# 重跑实测探针（需求文档附录 D 的数据来源）
& $py tools\probes\probe_verovio.py
& $py tools\probes\probe_layout.py
& $py tools\probes\probe_m4_layout.py
& $py tools\probes\probe_audio.py
& $py tools\probes\probe_webengine.py

# 浏览器引擎排障：探测结论分类 + 多套件切换逐行加载状态
& $py tools\diagnose_web_switch.py

# 浏览器引擎基准：默认(GPU) vs 软件渲染兜底（含"默认 profile 不得报 GPU 错"的验收）
& $py tools\probes\probe_webengine_perf.py --compare --rows 60
```

---

## 几个容易踩的坑（已处理，详见需求文档 §12.4 / §12.5）

1. **不要用 `getTimesForElement()` 构建全曲时间轴** —— 本机 verovio 6.3.0 对绝大多数 ID 返回全 0，
   且 29324 个 ID 逐个调用要 **132 s**。正确做法是用 `renderToTimemap()` 的 `on`/`off` 配对
   （全曲 **0.17 s**），并用 `getElementsAtTime()` 交叉验证。
2. **连音会产生"幽灵音符"** —— Verovio 把跨小节连音展开成 `<base>-rend2`，
   SVG 里找不到。已用 `getNotatedIdForElement()` + 区间重叠合并处理。
3. **`import fluidsynth` 会失败** —— DLL 已在 `tools/fluidsynth`，但需先调用
   `common.deps.prepare_fluidsynth()` 把目录加进搜索路径。实际音频渲染走
   `fluidsynth.exe -a file` 离线渲染（27× 实时），不依赖这个 import。
4. **渲染音频比乐谱长是正常的** —— FluidSynth 会输出混响/延音尾巴（实测比乐谱长 6–8%）。
   真正该校验的是 **MIDI 时长 vs `sync.json` 时间轴**（实测误差 < 10 ms）。
5. **MuseScore 4 加载 MusicXML 时不读 `midi-channel` / `midi-program`** ——
   所以多乐器音频链走 Verovio + FluidSynth；MuseScore 只用于 M5 的 PDF 排版。
6. **QtSvg 不能渲染 Verovio 的嵌套 `<svg>`** —— 它会报告 `isValid()==True`、尺寸正确，
   但画出来全是白的。必须先 `svg_geometry.flatten_svg()`（把内层 `<svg>` 变成 `<g>`）。
7. **不要在后台线程探测依赖** —— 探测会启动子进程，与主线程创建 `QSvgRenderer` 并发时
   会让进程 access violation。探测已改为同步且无子进程调用。
8. **`QMediaPlayer.positionChanged` 带 `qlonglong`**，不能直接连到 `Signal(int)`，
   必须经普通方法转发（否则 PySide6 报 `Failed to connect signal`）。
9. **`QSvgRenderer` 必须显式持有 Python 引用** —— `QGraphicsSvgItem` 只在 C++ 侧引用它，
   Python 包装被 GC 后会留下悬垂指针并崩溃。现用 `item.setData(0, renderer)` 持有。
10. **原生后端必须内联 `stroke`，否则五线谱的线全都看不见** —— Verovio 的谱线是
    `<path d="M1510 620 L7382 620" stroke-width="13"/>`，**没有 stroke 属性**，
    颜色来自内嵌 CSS `#id path {stroke:currentColor}`。当年的 QtSvg 不套用这条 CSS 规则，
    于是谱线没有描边（线条没有面积 → 完全不可见），而符头是实心字形所以照样显示 ——
    现象就是**"音符看得见、五线谱没线"**。`flatten_svg()` 现在会内联 stroke：
    深色像素 4501 → 9379，横贯谱线 0 → **5 条**。

    > **Qt 6.12.0 起这条坑已被上游填平**（2026-10-08 复验）：QtSvg 现在**会**套用内嵌 CSS
    > 并解析 `currentColor`。对照实验（`the-four-seasons-complete/svg/sys-0001.svg`）：
    > 内联 211651 深色像素 / 不内联 211204，横贯谱线**都是 60 行**；
    > 最小复现里 `#id path{stroke:currentColor}` 生效，而把选择器改成不匹配的 `#nope`
    > 则一个像素都不画 —— 证明它真的在做 CSS 选择器匹配，不是"默认给了黑描边"。
    > 因此 `inline_stroke_attributes()` 现在是**冗余但无害**的保险（只在元素没有显式
    > `stroke` 时才补，幂等、不覆盖 Verovio 指定的颜色），保留它可以让程序在 Qt < 6.12
    > 上照常工作。**`tests/test_m4_playback.py` 里那条反向护栏测试已按 Qt 版本分两路改写**
    > （`< 6.12` 断言"不内联必须看不见"、`>= 6.12` 断言"不内联也看得见"），
    > 两路都在断言可观测事实，任一侧翻转即失败。
11. **`PlayerState` 的值必须是 Qt 枚举的成员名（大写）** ——
    `str(QMediaPlayer.PlaybackState.PlayingState)` 得到 `"PlaybackState.PlayingState"`，
    最后一段是 **`PlayingState`**（大写 P），不是 `playingState`。
    早期写成小写导致 `if self.state != PlayerState.PLAYING: return` **永远成立**，
    `_tick()` 每帧直接返回 —— 现象就是**"播放时完全不高亮、不跟随"**。
    现在改为按枚举比较（`map_qt_state`），不做字符串解析。
12. **用 `QSvgRenderer` / `QPainter` 的测试必须提供 `QApplication`** ——
    否则 Qt 在 C++ 层直接 access violation（进程崩溃，pytest 连汇总都打不出来）。
    `tests/conftest.py` 提供了会话级 `qapp` fixture。
13. **切换套件时不要重启本地 HTTP 服务 / 不要换端口** —— WebEngine 的页面是长期存活的，
    换端口等于让页面在运行中改 origin，会出现"**该行加载失败**"。
    服务端实测 448/448 个 SVG 全部 200，问题在浏览器侧。
    现在服务只启动一次，切换套件只用 `set_root()` 换目录（端口恒定），
    并且每次切换都重载页面，换取干净的 DOM 状态。
    **默认后端已改为 `auto`**（浏览器引擎优先、失败回退原生 QtSvg），
    排障时可用 `native` 从根上避开这一类问题。
    若确实要用浏览器引擎，排障看日志里 `曲谱页面有 N 行加载失败` 那几行
    （会打印每行的失败原因与 URL），或直接跑 `tools/diagnose_web_switch.py`。
14. **行 SVG 的 URL 约定：`SystemRef.file` 是"相对套件目录"的路径**（`svg/sys-0001.svg`），
    两个后端都按它定位：原生后端读 `<root>/<file>`，WebEngine 后端请求
    `/sys/<相对路径>`（旧路由 `/svg/<名字>` 仍在）。**不要**只传文件名、
    也不要假设行文件一定在 `svg/` 下。踩坑记录：源文件预览把行平铺写进预览缓存根目录、
    又只给文件名，于是日志同时打印"MusicXML 预览完成：… 12 行"与
    "曲谱页面有 12 行加载失败：原因=HTTP 404" —— 服务端一个文件都找不到。
    现在预览缓存写成 `<cache>/svg/`（与套件同构），回归测试
    `tests/test_m5_preview.py::…test_preview_cache_layout_is_servable`
    与 `tests/test_local_server.py`（含 `..` 越界与扩展名白名单）。
15. **换默认后端 = 换实现：两个后端的接口必须逐名对齐** —— `ui/score_host.py` 用
    `hasattr` 探测曲谱视图的能力，**缺方法不报错、只是静默降级**。把默认后端从
    `native` 改成 `auto` 之后立刻踩到：`WebScoreView` 没实现
    `set_fit_width` / `fit_width_enabled` / `zoom_changed`，而
    `getattr(score_view, "_fit_width", True)` 又让它**看起来是开着的** ——
    现象就是「适应宽度」按钮勾选着、谱面却停在 100%、右侧一个横向滚动条。
    两个后端现已逐名对齐，并由 `tests/test_webengine_env.py` 的
    `REQUIRED_VIEW_API` 对照守住。**新增后端方法时只加在一边，这里会失败。**
16. **"GPU 报错"其实是自己关掉 GPU 造成的** —— 详见上面"排障：浏览器引擎的 GPU 报错"。
    旧版本无条件注入 `--disable-gpu` / `QT_OPENGL=software` / `QT_QUICK_BACKEND=software`
    并把它归因于显卡，实测三项全错（`QT_QUICK_BACKEND=software` 与 `--disable-gpu`
    才是报错来源，且都非致命）。现在默认 profile 不关 GPU，只有探测确认
    "建不出 GL 上下文"时才降级软件渲染。
17. **`adjustPageHeight` 早先被我误判为有害** —— M4 中期曾写"它会把乐谱压扁"，
    其实那是 `scale=40` + `pageHeight=400` 共同造成的（页高放不下一个 system，
    Verovio 只能整体缩小）。在 `scale=100` 下实测：开启后**高度贴合内容、
    字号完全不变**（rachmaninoff 2100×1500 → 2100×1439，符头仍 22.7 px）。
    现默认**开启**。若不开，每个 system 会固定成 2100×1500，
    内容下方留 400–1000 px 空白。
18. **固定页宽的 SVG 必须做"宽度自适应"** —— 曲谱按 `page_width=2100` 渲染，
    而窗口常只有 900–1600 px，1:1 显示必然出现横向滚动条与两侧留白，
    且当时**没有任何缩放 UI**（用户反馈"不能手动缩小"）。
    现在视图默认按视口宽度自适应，并提供缩放工具条（30%–400%）与
    `Ctrl+=` / `Ctrl+-` / `Ctrl+0` / `Ctrl+W` 快捷键。
    **两个后端都必须实现**（原生 `QGraphicsView.scale()`；浏览器引擎
    `setZoomFactor()`，见第 15 条），否则"适应宽度"会退化成"永远 100%"。
    另注意 WebEngine 页面里 `#systems` 用的是 `align-items: safe center`：
    普通 `center` 在谱面比视口宽时会把溢出平均分到两侧，**左边缘滚不回来**
    （实测 2100px 谱面 / 1200px 视口：`.sys.left = -458`、`scrollWidth` 只有 1642）。
