# 音乐曲谱工具软件需求说明书

> 软件代号建议：**zpyMusic**
> 文档版本：v2.0（在 v1.0 讨论稿基础上完善）
> 修订日期：2026-02（环境实测记录见附录 A）

## 修订记录

| 版本 | 变更摘要 |
| :--- | :--- |
| v1.0 | 初始设想：三界面、以 MusicXML 为中心的多格式转换、SVG 同步高亮；末章给出实施计划 |
| v2.0 | 明确技术选型与依据；把 v1.0 的每一处"问号"变成可决策的结论；补充套件（suite）目录与文件规范、`sync.json` 数据契约、同步算法、格式转换能力矩阵与可行性分级、界面与交互规范、异常与日志规范、性能指标、能力极限清单、测试样本与验收标准、风险与里程碑、待确认事项；附录记录本机实测环境与关键 API |
| v2.1 | **按 M0–M2 实测结果回填**：新增「十二、实现现状」章节；修正 §5.1.1（套件内 SVG 改为 `svg/` 子目录 + 一行一个文件）与 §5.1.4（`schema` 升到 `zpymusic-sync/1.1`，notes 用紧凑键，新增 `systems`/`measures`）；§3.1 更新环境（依赖已安装、FluidSynth 已就位）；附录 B 用实测纠正 `tstamp` 单位并标出 `getTimesForElement` 的坑；新增附录 D（Verovio 实测数据）与附录 E（可复现验证命令） |
| v2.2 | **按 M4（同步播放）实测结果回填**：§5.3 标注 F2.1–F2.9 已实现；§12.1/§12.2 更新里程碑状态；新增 §12.5「M4 的实测修正」（`scale` 与 `adjustPageHeight` 的版面纠偏、SVG 嵌套结构、QtSvg 的两个致命坑、线程竞争崩溃、`QMediaPlayer` 信号签名）；附录 D 增补 D.5「M4 版面与渲染实测」；§8.2 验收标准逐条标注实测结论 |
| v2.5 | **M7：高亮策略改为"跟随当前小节"**（用户反馈：高亮跟着每个音符走）。§5.3 的 F2.6/F2.7 与同步算法据实改写；新增 §12.8 记录实现方案与实测数据（小节矩形取自 SVG 的 `<g class="measure">`，10 个套件 1958 个小节全部可定位、0 处回退） |
| v2.6 | **M5 需求变更：源文件「预览」（新增 F3.8）** —— 源文件行「选择…」左侧新增「预览」按钮，MusicXML/SVG 显示乐谱、MIDI/MP3 试听，均为非模态窗口；§5.4.2 增补该需求，新增 §12.9 记录实现与实测（含一个 Verovio 的坑：工作线程里建 toolkit 会找不到字体资源，须显式 `setResourcePath`） |
| v2.7 | **M5 完成：格式转换全量落地** —— 新增 `core/convert.py`（能力矩阵 + 依赖自检 + 命名模板 + 12 条转换实现，GUI 与 CLI 共用），「格式转换」页补齐 F3.2–F3.7（依赖自检/保真度提示/批量列表/后台执行与取消/结果区/命名模板）；§5.4.1 逐条标注实测、§5.4.2 标注 F3.* 状态；新增 §12.10 记录实现与两个坑（套件形态判定、MuseScore 继承 `QT_QPA_PLATFORM` 崩溃） |
| v2.8 | **M6：纠正"浏览器引擎 GPU 报错"的归因** —— 对照实验证明那两条 `gpu_channel_manager` 报错由程序自己注入的 `--disable-gpu` / `QT_QUICK_BACKEND=software` 产生（非硬件/RDP/虚拟机，且非致命）。`prepare_webengine_env()` 改为"默认 profile 不关 GPU + 两段式探测 + 仅 GL 失败才降级软件渲染"，移除 `--no-sandbox` / `QTWEBENGINE_DISABLE_SANDBOX`，`run_gui()` 不再无条件注入；默认后端 `native` → `auto`；新增 §12.11 与回归测试 `tests/test_webengine_env.py` |
| v2.9 | **M6：补齐 WebEngine 后端的缩放 / 适应宽度** —— 默认后端改成 `auto` 后立刻暴露：`WebScoreView` 没有实现 `set_fit_width` / `fit_width` / `fit_width_enabled` / `zoom_changed`，而 `ui/score_host.py` 用 `hasattr` 探测能力 → **静默降级**成"按钮已勾选但什么都不做"，谱面永远 100%。顺带修掉页面 CSS 的 `align-items:center` 导致"谱面比视口宽时左边缘滚不回来"；新增 §12.12 与后端接口一致性回归测试 |

---

## 一、项目范围

### 1.1 目标

以 **MusicXML（含压缩格式 `.mxl`）为唯一权威数据源**，实现曲谱在 `musicxml / midi / mp3 / svg / pdf` 之间的转换，并在播放 `midi / mp3` 时于 SVG 曲谱上同步高亮当前发音音符。GUI 包含三个主界面（标签页形式）：

1. **生成套件**：从 MusicXML 生成对应的 SVG / MIDI / MP3，可选择渲染 MP3 的音色库（sf2 / sf3）。输出一个乐曲的 `musicxml + svg + midi + mp3 + sync.json` 套件（suite）。
2. **同步播放**：播放套件中的 MIDI / MP3，展示 SVG 曲谱，并同步高亮当前发音音符。
3. **格式转换**：在 `musicxml / midi / mp3 / svg / pdf` 之间转换，选择源文件与目标文件夹。

### 1.2 设计公理（贯穿全部功能）

1. **MusicXML 是"主副本"**：它承载记谱、布局、乐器、速度等全部信息。MIDI / MP3 是面向播放的**导出产物**；SVG 是面向显示的**导出产物**；PDF 是面向分享的**导出产物**。
2. **有损 / 增维转换必须对用户可见**：`MusicXML → MIDI/MP3` 是信息降维（记谱、连音线、歌词、力度记号、排版全部丢失）；`MIDI/MP3 → MusicXML` 是信息增维（小节、拍号、调号、时值都靠**推断**）。任何反向转换都必须视为"生成一份新的乐谱"，不可伪装成"还原"。
3. **时间轴与乐谱必须"同源"**：同步高亮所用的时间轴，必须与生成音频所用的时间模型出自**同一次渲染**，否则高亮会随速度变化而漂移。这是本项目最关键的技术约束，详见 §4。
4. **一切耗时的外部依赖都必须可探测、可降级**：MuseScore / FluidSynth / ffmpeg 缺失时给出明确提示，而不是静默产出错误结果。

### 1.3 范围界定

**本期包含（In Scope）**

- MusicXML（`.xml / .musicxml / .mxl`）解析、校验与规范化。
- SVG 生成（含音符级 ID 与时间轴数据）。
- MIDI 生成；MIDI → MP3 渲染（SoundFont）。
- 正向转换：`musicxml → svg / midi / mp3 / pdf`。
- 反向转换：`midi → musicxml`、`svg → musicxml`（限本工具生成的 SVG）。
- `pdf → svg`、`midi → mp3`、`mp3/midi → wav`。
- 套件管理与同步播放高亮。

**本期不包含（Out of Scope，或列为远期）**

- 乐谱**编辑**（改音符、改调号）——本工具是转换与展示工具，不是打谱软件。
- 音频智能识别（`mp3 → musicxml` 的自动转谱），见 §7.4。
- `pdf → musicxml` 的光学识别（OMR），见 §7.4。
- 多声部音频分轨、实时录音、视频导出。

---

## 二、术语

| 术语 | 含义 |
| :--- | :--- |
| **套件（suite）** | 一个乐曲的全套产物集合：源 MusicXML + SVG + MIDI + MP3 + `sync.json`，同目录、同主名 |
| **主名（base name）** | 源文件名去掉扩展名后的部分，套件内所有文件共用 |
| **timemap / 时间轴** | 音符 ID ↔ 播放时间（毫秒）的映射表 |
| **sync.json** | 本工具自有的同步数据文件（见 §5.1.4） |
| **音节 / 音符事件** | 一个可高亮的最小单位；对和弦指整组音符，对休止符默认可选高亮 |
| **GM 音色** | General MIDI 标准音色号（0–127），MIDI Program Change 的目标 |
| **SoundFont** | `.sf2 / .sf3` 音色库文件，供 FluidSynth 渲染 MIDI 为音频 |
| **有损 / 推断** | 见 §1.2 第 2 条 |

---

## 三、运行环境与依赖

### 3.1 本机实测环境（已验证，见附录 A）

| 项 | 实测结果 |
| :--- | :--- |
| Python | 3.13.16 @ `C:\miniconda3\envs\ibase\python.exe`（conda 环境名 `ibase`，是 `C:\miniconda3\envs` 下唯一环境） |
| 已装包 | `PySide6 6.9.3`（含 Addons / Essentials）、`numpy 2.5.3`、`scipy 1.18.1`、`mido 1.3.3` |
| 新增依赖（M0 已安装） | `verovio 6.3.0`、`music21 10.5.0`、`pyfluidsynth 1.4.0`、`pytest` |
| FluidSynth（M0 已就位） | `zpymusic/tools/fluidsynth/`：`fluidsynth.exe`、`libfluidsynth-3.dll`、`SDL3.dll`、`sndfile.dll`（FluidSynth 2.6.1 官方 Windows x64 版）。`import fluidsynth` 仍会失败，**必须**由 `common/deps.prepare_fluidsynth()` 先把 DLL 目录加入搜索路径 |
| 打谱引擎 | **MuseScore 4 已安装**：`C:\Program Files\MuseScore 4\bin\MuseScore4.exe`（已验证可执行，CLI 支持 `-o` 导出与 `-T/--trim-image`） |
| 音频工具 | **ffmpeg 已安装**：`C:\ffmpeg\bin\ffmpeg.exe`（版本 N-122544，**含 `libmp3lame`**，可直接编码 MP3） |
| 音色库 | `zpymusic/sound/`：`MuseScore_General.sf3`（39 MB，MIT）、`MS Basic.sf3`（51 MB，MIT，MuseScore_General 精简版）、`GeneralUser GS v1.471.sf2`（31 MB）、`GeneralUser GS MuseScore v1.442.sf2`（31 MB）、`FluidR3_GM2-2.SF2`（148 MB） |
| 测试样本 | `zpymusic/staff/musicxml/` 下 10 个 **压缩 MusicXML（`.mxl`，ZIP 容器）**，最大 `the-four-seasons-complete.mxl` 347 KB |

> **重要**：现有样本全部是 `.mxl`（ZIP 压缩包），且 `META-INF/container.xml` 指向的根文件命名不统一（`score.xml` 或 `lg-*.xml`）。因此**解析层必须先按 ZIP 容器规范解出真正的根 XML**，不能假设文件名。

### 3.2 待新增依赖

| 依赖 | 用途 | 结论 / 依据 |
| :--- | :--- | :--- |
| **`verovio`** | MusicXML → SVG（音符级 ID）、MusicXML → MIDI、时间轴 | **核心新增依赖，强烈推荐。** PyPI 有 Windows 预编译轮子（实测解析到 `verovio-6.3.0-cp310-abi3-win_amd64.whl`，`abi3` 兼容 Python 3.13，无需编译器） |
| **`music21`** | MIDI → MusicXML、调性/结构分析、乐理级校验 | 反向转换主力（当前 PyPI 10.5.0） |
| **`pyfluidsynth`** | MIDI → WAV/MP3（SoundFont 渲染） | 纯 Python 包（1.4.0），需配套 `libfluidsynth` 动态库（见 §3.3、风险 R1） |
| `pypdf` / `pypdfium2`（可选） | PDF 元数据读取、PDF 内嵌 MusicXML 附件提取 | 仅 PDF 相关高级功能需要 |

**不推荐**：`midi2audio`（2019 年后基本停更），直接用 `pyfluidsynth` 或 `fluidsynth` 命令行更可控。
**不必要**：`pydub`（只是 ffmpeg 的封装，本项目直接调 ffmpeg 子进程即可，少一层依赖）。

安装命令（在 `ibase` 环境）：

```powershell
C:\miniconda3\envs\ibase\python.exe -m pip install verovio music21 pyfluidsynth
```

### 3.3 外部工具与配置探测

程序启动时自动探测，结果写入日志（缺失项以**警告**呈现，不阻塞启动）：

| 工具 | 默认候选路径 | 缺失影响 |
| :--- | :--- | :--- |
| MuseScore 4 | `C:\Program Files\MuseScore 4\bin\MuseScore4.exe` | 无法生成 `pdf`；失去 SVG 备选渲染器 |
| ffmpeg | `C:\ffmpeg\bin\ffmpeg.exe` | 无法 `wav → mp3`、`mp3 → wav` |
| FluidSynth | `pyfluidsynth` 可导入 / PATH 上的 `libfluidsynth*.dll` / `fluidsynth.exe` | 无法 `midi → mp3`、`midi → wav`（**核心功能受影响**） |
| Verovio | Python 包（`import verovio`） | 无法生成带音符 ID 的 SVG（**核心功能受影响**） |

配置项持久化到 `zpymusic/config.json`（首次运行自动生成），用户可在"设置"中修改：

```json
{
  "paths": {
    "musescore": "C:/Program Files/MuseScore 4/bin/MuseScore4.exe",
    "ffmpeg": "C:/ffmpeg/bin/ffmpeg.exe",
    "fluidsynth_dll": null,
    "verovio_resources": null
  },
  "audio": {
    "default_soundfont": "sound/MuseScore_General.sf3",
    "sample_rate": 44100,
    "gain": 0.6,
    "reverb": true,
    "chorus": true,
    "keep_wav": false
  },
  "render": {
    "engine": "verovio",
    "scale": 100,
    "page_width": 2100,
    "svg_additional_attributes": ["note@pname", "note@oct", "note@dur"]
  },
  "ui": {
    "tab_position": "west",
    "highlight_color": "#FF8C00",
    "highlight_opacity": 0.35
  }
}
```

### 3.4 音色库选择建议

| 音色库 | 大小 | 授权 | 定位 |
| :--- | :--- | :--- | :--- |
| **`MuseScore_General.sf3`** | 39 MB | MIT | **默认推荐**：GM 覆盖全、体积/质量平衡最好、授权干净 |
| `MS Basic.sf3` | 51 MB | MIT | 低配机器备选（CPU / 内存占用更低） |
| `GeneralUser GS v1.471.sf2` | 31 MB | 见原包说明 | 体积小、音色明亮，作为备选 |
| `FluidR3_GM2-2.SF2` | 148 MB | 见原包说明 | 音质优先（GM2），渲染慢、内存占用高 |

**决策**：不预设"哪个更好"，而是提供**音色库下拉框**（扫描 `sound/` 目录，显示名称、体积、格式），并把选择**记录进套件**（`sync.json.audio.soundfont`），保证同一套件可复现。同时允许覆盖 `sample_rate` / `gain` / `reverb` / `chorus` 等渲染参数。

> `.sf3` 是经 Vorbis 压缩的 SoundFont，文件更小但需 FluidSynth 2.x 支持。启动时用实际加载做可用性测试，不可用则回退到 `.sf2`。

### 3.5 是否需要 ffmpeg？

**需要，但用途有限**：仅用于编解码压缩音频（`wav ↔ mp3`，以及可选 `flac/ogg`）。**MIDI → 音频这一步与 ffmpeg 无关**，由 FluidSynth 完成。已确认本机 ffmpeg 带 `libmp3lame`，因此不需要额外准备 MP3 编码器。

---

## 四、关键技术决策

| 编号 | 决策 | 理由 |
| :--- | :--- | :--- |
| **D1** | **SVG 渲染主引擎 = Verovio**（MuseScore 4 负责 PDF 与备选 SVG） | Verovio 输出的 SVG **保留 MEI 树结构**：MEI `<note xml:id="n1">` 在 SVG 中成为 `<g class="note" id="n1">`，可直接用 CSS / DOM 选择器高亮，并可用 `svgAdditionalAttribute` 把 `note@pname/@oct` 暴露为 `data-*` 供选择器使用。而 MuseScore 是"导出 SVG 图片"，其原生高亮靠内部元素重绘（详见 `docs/predo.md`） |
| **D2** | **时间轴 = Verovio 时间模型**，不是"从 MIDI 反解" | `getTimesForElement(id)` 一次返回 `scoreTimeOnset/Offset` 与 `realTimeOnsetMilliseconds/OffsetMilliseconds`；`getElementsAtTime(ms)` 直接给出"此刻正在响的音符 ID"；且 `renderToMIDI()` 与 `renderToTimemap()` **出自同一次解析、同一速度模型**，从根上消除"音频与谱面不同源"的漂移 |
| **D3** | **音频渲染 = FluidSynth + SoundFont**，输入 **Verovio 导出的 MIDI** | 保证 D2 成立：`midi`、`mp3`、`timemap` 三者同源。MIDI 天然支持多轨/多通道，多乐器信息**可以**保留（纠正 v1.0 的疑问，见 §7.4） |
| **D4** | **同时输出 WAV 作为中间产物** | FluidSynth 渲染到 WAV，再 ffmpeg 编码 MP3。WAV 是无损中间态，便于换码率重编码与直接试听；是否保留由用户勾选（默认"生成后删除"） |
| **D5** | **SVG 展示控件 = `QWebEngineView`（QtWebEngine），而非 QSvgWidget** | ① DOM 级高亮（`classList.add("zpy-hl")`）性能远好于整图重绘；② 可直接复用 Verovio 的 CSS 用法；③ 缩放、滚动、点击命中（`elementFromPoint`）都现成。`PySide6-Addons` 已安装，**零额外依赖**。若部署体积敏感，再降级为 `QGraphicsSvgItem` + 手动坐标查找（见风险 R4） |
| **D6** | **音频播放 = `QMediaPlayer`（Phase 1）→ `sounddevice`（Phase 2 可选）** | `QMediaPlayer` 支持 `position()` / `setPlaybackRate()`，零额外依赖、开发快，但位置精度约 ±100 ms。因此 **Phase 1 目标同步误差 ≤ 100 ms**，Phase 2 换 `sounddevice`（对 WAV 精确 seek）冲击 ≤ 30 ms。播放后端抽象为 `PlaybackBackend` 接口，便于替换 |
| **D7** | **`mp3 → musicxml` 与 `pdf → musicxml` 不做自动识别** | 见 §7.4：给出替代路径与外部工具桥接方案，而不是假装能做 |
| **D8** | **主名（base name）= 源 MusicXML 去扩展名**，套件内所有产物同主名 | 满足"与源文件同名"的诉求，并保证套件可被程序按主名发现 |

---

## 五、功能需求

### 5.1 通用：套件（suite）规范

#### 5.1.1 目录结构

```
staff/
  musicxml/  <源 MusicXML 库，用户可自选任意源目录>
  suites/
    <主名>/
      <主名>.musicxml     ← 规范化后的源文件副本
      <主名>.mxl          ← 原始压缩源（若源为 .mxl，用于溯源）
      svg/sys-0001.svg …  ← 渲染结果：**一个 system（一行谱）一个文件**（见 5.1.2）
      <主名>.mid          ← Verovio 导出
      <主名>.mp3          ← FluidSynth + ffmpeg 生成
      <主名>.wav          ← 可选中间产物
      <主名>.sync.json    ← 同步数据（时间轴 + 元数据）
  midi/  mp3/  svg/  pdf/  ← 转换界面的默认输出目录（保持现有目录布局）
```

- 默认套件根目录 `staff/suites`，用户可改。
- **SVG 放在 `svg/` 子目录、一行一个文件**（M0 实测调整，原因见 §5.1.2）：文档初稿写的是
  单文件 `<主名>.svg`，但大曲目（four-seasons 有 276 个 system）无法单文件承载。
- 目录已存在时：默认"跳过已存在文件并提示"，另提供"覆盖"与"新建带序号目录"选项。
- 同名冲突（不同源目录下有同名文件）在界面上提示，由用户决定覆盖或改名。

#### 5.1.2 SVG 版面（M0 实测后重新决策）

M0 对五个候选选项集在 3 个规模不同的样本上做了对比试验，结论如下（数据见附录 D）：

| 方案 | canon | four-seasons | 评价 |
| :--- | :--- | :--- | :--- |
| `breaks=smart`（默认尺寸） | 2 页 840×1188 | 98 页 840×1188 | 页面是 A4 形状，不是"紧凑的行" |
| `breaks=none` + `pageHeight=20000` | 1 页 **7145×191** | 1 页 **167972×568**（23 MB，渲染 16.7 s） | 横向铺开，尺寸失控 |
| **`breaks=smart` + `pageHeight=400` + `adjustPageHeight=True`** | **11 页 840×186** | **276 页 840×406** | ✅ **采纳** |

**最终约定**：

- 默认 **`breaks="smart"` + `pageHeight=400` + `adjustPageHeight=True`**：Verovio 把
  **每一个 system（一行谱）渲染成一个宽度恒定、高度贴合内容的 SVG**。
- 文件命名 `svg/sys-0001.svg`（固定 4 位序号，便于按名排序）。
- `sync.json.systems[]` 记录每个 system 的文件名、宽高、首尾元素 ID 与**该行包含的全部元素 ID**，
  播放界面据此按行懒加载、并在跟随播放时只加载需要的行。
- 需求 Q2 的字面选项"单页连续纵向"仍保留为 `render.single_page=true`：
  小曲目（canon）确实是 1 页 7145×191；**但大曲目会输出 167972×568 的超大 SVG**，
  因此只建议在导出/打印场景使用，默认关闭。

#### 5.1.3 MusicXML 的规范化

为消除 `.mxl` 与各种非标准 MusicXML 带来的解析差异，生成套件时统一规范化：

1. 按 ZIP 容器读取 `META-INF/container.xml`，取 `rootfile@full-path` 指定的根 XML（**不可假设固定文件名**）。
2. 校验根元素是否为 `score-partwise` 或 `score-timewise`；`score-timewise` 先转换为 `score-partwise`。
3. 去 BOM、统一换行、保留原始 DOCTYPE / 命名空间。
4. 输出 `<主名>.musicxml`（UTF-8），同时保留原始 `.mxl` 以便溯源。
5. 解析失败时明确报错并指出 XML 路径（如 `/score-partwise/part-list/score-part[3]`），**不生成后续产物**。

#### 5.1.4 `sync.json` 数据契约

`sync.json` 是**播放界面唯一依赖的数据源**，必须自带版本号以便演进：

```json
{
  "schema": "zpymusic-sync/1.1",
  "source": { "file": "canon-in-d-easy.mxl", "base": "canon-in-d-easy", "sha1": "...",
              "title": "Canon in D", "composer": "Pachelbel", "part_count": 1 },
  "render": {
    "engine": "verovio", "engine_version": "6.3.0",
    "scale": 40, "page_width": 2100, "breaks": "smart", "page_height": 400,
    "adjust_page_height": true, "single_page": false,
    "system_count": 11,
    "svg_files": ["svg/sys-0001.svg", "…", "svg/sys-0011.svg"]
  },
  "audio": {
    "soundfont": "sound/MuseScore_General.sf3", "sample_rate": 44100,
    "gain": 0.6, "reverb": true, "chorus": true, "bitrate_kbps": 192,
    "duration_ms": 127200, "offset_ms": 0.0
  },
  "parts": [
    { "id": "P1", "name": "Piano", "part_name": "Piano", "instrument": "",
      "midi_channel": 1, "midi_program": 0, "program_name": "Acoustic Grand Piano",
      "explicit_program": true }
  ],
  "tempo": { "initial_bpm": 100.0, "tempo_marks": [{ "at_ms": 0, "bpm": 100.0 }] },
  "systems": [
    { "index": 1, "file": "svg/sys-0001.svg", "width": 840, "height": 186,
      "first_id": "d9g3ld5", "last_id": "…", "note_ids": ["d9g3ld5", "…"] }
  ],
  "measures": [ { "id": "oi0krdr", "onset_ms": 0, "qstamp": 0 } ],
  "notes": [
    { "id": "iop1zij", "s": 0, "d": 300, "m": "oi0krdr" },
    { "id": "px4k2ab", "s": 300, "d": 300, "q": 0.5, "m": "oi0krdr" },
    { "id": "n7fq1cd", "s": 900, "d": 1200, "x": 1, "m": "oi0krdr" }
  ]
}
```

**`notes` 字段用紧凑键**（原因：four-seasons 有 **29324** 条，长键会让文件明显膨胀）：

| 紧凑键 | 含义 |
| :--- | :--- |
| `id` | 元素 ID（**已归一到可在 SVG 中找到的原始 `xml:id`**，见下） |
| `s` | `onset_ms`，毫秒 |
| `d` | `dur_ms`，毫秒 |
| `q` | `qstamp`（可选，四分音符数，自检用） |
| `r` | 取 1 表示 `is_rest`（默认不写，即都是音符） |
| `m` | 所属小节 ID（可选） |
| `x` | 取 1 表示该 ID 原本是 Verovio 展开产生的 `*-rendN` 元素（可选） |

读取端**同时兼容文档初稿的长键**（`onset_ms` / `dur_ms` / `is_rest` / `measure` / `rendered`），
见 `core/sync_model.py::_expand_note`。

> **`-rendN` 归并（M0 发现并修复的坑）**：Verovio 会把跨小节的连音展开成
> `<base>-rend2` 之类的新元素 ID。若不处理，它们会作为"幽灵音符"进入时间轴
> ——实测 canon 里有 **29 个**这样的 ID，既在 SVG 里找不到，又与 `<base>` 重复计数。
> 处理方式（`score_render.py`）：
> 1. 用 `toolkit.getNotatedIdForElement()` 把所有 ID 规范化回原始 `xml:id`；
> 2. 同一规范化 ID 的**时间区间互相重叠时才合并**——连音的多个片段合成一个事件，
>    而重复演奏的同一个音符因区间不重叠仍保持为两个事件。
> 实测效果：10 个样本的 `sync.json` 音符 ID **100% 都能在 SVG 中找到**（见附录 E）。

- `notes` 按 `onset_ms` 升序（播放时二分查找）。
- `offset_ms` 是**用户可调的全局校准值**（音频设备缓冲补偿），播放时 `查询时间 = player.position() × playbackRate + offset_ms`。
- 和弦：若 `GetElementsAtTime` 返回 `chord` 元素 ID，则额外记录 `chord_members`；规则是"高亮整个和弦组"。
- 可选包含休止符（`includeRests`），用于"高亮当前小节"类需求。

### 5.2 功能一：生成播放套件

用于为功能 5.3（同步播放）提供数据支撑。

| 编号 | 需求 | 说明 / 验收 |
| :--- | :--- | :--- |
| **F1.1** | 选择源 MusicXML（单文件 / 批量目录） | 支持 `.xml / .musicxml / .mxl`；批量时逐文件排队，单项失败不中断整体 |
| **F1.2** | 选择目标文件夹 | 默认 `staff/suites`；展示"将生成的文件清单"预览 |
| **F1.3** | 生成带音符 ID 的 SVG | 每个可发音元素在 SVG 中具备与 MEI `@xml:id` 相同的 `id`；CSS 类名含 MEI 元素名（`note` / `chord` / `rest` / `measure`） |
| **F1.4** | 生成 MIDI | 由同一 Verovio 实例导出，与 F1.5 同源 |
| **F1.5** | 生成 `sync.json` | 满足 §5.1.4 契约；做"音符数 vs MIDI note-on 事件数"一致性检查（差异 > 2% 时警告） |
| **F1.6** | 生成 MP3（可选音色库） | FluidSynth 渲染 WAV → ffmpeg 编码 MP3；默认 44.1 kHz / 192 kbps / 立体声；可勾选保留 WAV |
| **F1.7** | 复制源 MusicXML 到套件目录 | 满足"与源文件同名"的要求；同时保留原始 `.mxl` |
| **F1.8** | 多乐器自动音色映射 | 解析 `part-list` 的 `midi-instrument/midi-program` 作为默认映射，展示为**可编辑表格**（声部名 / 当前 GM 音色 / 试听），支持手动改音色与通道；无 `midi-program` 时按 GM 0（钢琴）并提示"未指定音色，请确认" |
| **F1.9** | 进度与可取消 | 渲染 / 合成阶段显示进度（按页、按秒），支持取消；取消后清理半成品文件 |
| **F1.10** | 覆盖策略 | 跳过 / 覆盖 / 新建带序号目录（见 §5.1.1） |

**流程**（单文件）：

```
选择 .mxl / .musicxml
  → 规范化（§5.1.3）→ 确定主名
  → Verovio: loadData → renderToMIDI（必须先调用）→ getPageCount → 逐页 renderToSVG
  → Verovio: renderToTimemap + 逐音符 getTimesForElement → sync.json
  → FluidSynth: <主名>.mid → <主名>.wav
  → ffmpeg:    <主名>.wav → <主名>.mp3
  → 复制源文件 → 写日志
```

> **调用顺序注意**：Verovio 的 `getTimesForElement()` / `getMIDIValuesForElement()` **要求先调用 `renderToMIDI()`**。因此固定顺序为：`loadData → renderToMIDI → renderToTimemap → renderToSVG(1..n) → getTimesForElement(逐个)`。

### 5.3 功能二：同步播放展示曲谱

| 编号 | 需求 | 说明 / 验收 |
| :--- | :--- | :--- |
| **F2.1** | 选择套件 / 音频 | ✅ 已实现（左侧扫描套件列表 + 直接选音频） |
| **F2.2** | 缺件提示 | ✅ 已实现。找不到配套 SVG 时：**提示且不播放**（遵循 v1.0 要求）；找不到 `sync.json` 时：允许播放但**禁用高亮**并给出降级提示 |
| **F2.3** | 播放控制 | ✅ 已实现（空格键）。`播放 / 暂停`（合并为一个按钮）+ `停止`；空格键 = 播放/暂停 |
| **F2.4** | 进度条 | ✅ 已实现（拖动时实时预览高亮；点击谱面跳转）。可拖动 seek；拖动时实时预览目标位置的高亮；在 SVG 上点击某音符 → 跳到该音符起点 |
| **F2.5** | 速度调节 | ✅ 已实现（速率档位下拉 + `</>` 快捷键 + 等效 BPM 只读显示）。**方案**：播放速率 50%–200%（步进 1%，快捷键 `<` `>`，一键回 100%），并实时换算显示等效 BPM（只读）。**Phase 2 可选**：真实改 BPM → 重生成 MIDI/MP3 → 重新载入（慢，但产物本身改变） |
| **F2.6** | 同步高亮 | ✅ 已实现（M7 起**高亮当前小节**：一个盖住整小节的半透明矩形，宽度 = 小节宽度、高度 ≈ 该行谱表高度；原生后端画覆盖框，WebEngine 后端在 SVG 里插入 `<rect>`）。支持自定义**颜色**（取色器）与**透明度**（0–100% 滑杆）；套件没有小节数据时自动退回"高亮当前发音音符/和弦" |
| **F2.7** | 光标跟随 | ✅ 已实现（可开关）。自动滚动 / 自动翻页跟随**当前小节**所在的那一行；可开关；多页 SVG 时自动切页 |
| **F2.8** | 手动校准 | ✅ 已实现（含「按当前音符校准」按钮）。提供 `offset_ms` 微调（±500 ms，步进 10 ms）与"以当前音符为基准对齐"按钮，补偿音频缓冲延迟 |
| **F2.9** | 状态显示 | ✅ 已实现。当前时间 / 总时长、当前小节号、BPM（或速率）、当前音色库、当前套件路径 |
| **F2.10** | 无 `sync.json` 的兜底 | ⏳ 未实现（当前行为：允许播放但禁用高亮并提示）。 若同目录存在"由本工具生成"的 SVG，尝试从 SVG 的 `data-onset-ms` 属性重建映射；否则禁用高亮 |

**同步算法**（核心，独立为可测试模块）：

```
状态：notes[]（按 onset_ms 升序）、measures[]（按 onset_ms 升序）、
      activeIds（上一帧发声集合，供状态栏/校准）、currentMeasure（上一帧高亮的小节）、
      offset_ms、rate

每帧（定时器 16–33 ms）：
  t   = player.position() * rate + offset_ms
  ids = binarySearchActive(notes, t)      # 取 onset_ms <= t < onset_ms + dur_ms 的所有音符
  if ids != activeIds: activeIds = ids     # 仅用于状态栏显示与"按当前音符校准"

  mid = measureAt(measures, t)             # 当前小节（二分查找）
  if mid != currentMeasure:                # ★ 只有换小节才重画（M7 起的主策略）
      currentMeasure = mid
      高亮 = 覆盖 mid 的矩形（宽度 = 小节宽度、高度 ≈ 谱表高度）
      原生后端：画出覆盖框；WebEngine：给该小节的 <g class="measure"> 插入/更新 <rect>
      if follow: 滚动到该小节所在行（仍在舒适区内则不动，见 §12.7）
  updateProgress(t)
```

- **高亮单位是小节，不是音符**（v2.5 起）。理由：① 跟读乐谱时人的视线落在小节上，
  逐音符闪烁反而干扰；② 30 fps 下"每音符一次重绘"变成"每小节一次"，
  长曲子（如 `the-four-seasons-complete`，1136 小节）几乎不再有高亮相关的重绘。
- 小节矩形的来源是 Verovio SVG 里的小节容器 `<g class="measure" id="<小节 xml:id>">`
  —— 它的 `id` 就是 `sync.json.measures[].id`，包围盒天然等于"小节宽度 × 该行谱表高度"。
- 小节 → 行的归属由 `sync.json`（`notes[].measure`）算出，**不需要解析 SVG**：
  跳到还没挂载的行时也能先定位，视图再按需挂载该行取矩形。
- 高亮样式集中在注入的 CSS 中，便于用户改颜色与透明度：
  ```css
  /* 小节高亮（主策略） */
  rect.zpy-hl-measure { fill: var(--zpy-hl-color); stroke: var(--zpy-hl-color);
                        opacity: var(--zpy-hl-alpha); pointer-events: none; }
  /* 逐音符高亮（没有小节数据的旧套件才用） */
  g.note.zpy-hl, g.chord.zpy-hl {
    fill: var(--zpy-hl-color); color: var(--zpy-hl-color); opacity: var(--zpy-hl-alpha);
  }
  ```
- 只做"增量增删 class / 增删一个 rect"，不整树重绘；1000+ 音符单页也应流畅。
- 拖动 seek 时重置 `cursor` 并全量重算一次（`force=True`）。

### 5.4 功能三：格式转换

#### 5.4.1 能力矩阵

| 转换 | 优先级 | 实现方式 | 依赖 | 保真度 / 备注 |
| :--- | :--- | :--- | :--- | :--- |
| `musicxml → svg` | P0 | ✅ Verovio（复用套件流水线，产物是可播放套件目录） | verovio | 高（结构 + 时间轴）。SVG 按行输出，便于播放界面懒加载 |
| `musicxml → midi` | P0 | ✅ Verovio `renderToMIDI` | verovio | 演奏信息高；记谱信息丢失。实测 canon 0.2 s |
| `musicxml → mp3` | P0 | ✅ 上述链路 + FluidSynth + ffmpeg | 三者 | 取决于音色映射与音色库。实测 canon 6.0 s |
| `musicxml → pdf` | P1 | ✅ **MuseScore 4 CLI**：`MuseScore4.exe -f -o out.pdf in.musicxml`（子进程必须清掉 `QT_QPA_PLATFORM`，见 §12.10） | MuseScore 4 | 排版质量最好。备选：Verovio SVG → PDF（`cairosvg` / `rsvg`，需新增依赖）；**不用**"SVG → 图片 → PDF 拼接"（会丢矢量）。实测 canon 0.7–0.9 s / 60 KB |
| `musicxml → wav` | P1 | ✅ FluidSynth（`midi_to_wav`） | FluidSynth + 音色库 | 无损中间态。实测 canon 4.9 s（27× 实时） |
| `midi → musicxml` | P1 | ✅ `music21`（`converter.parse` → `write('musicxml')`），默认量化（可关） | music21 | **推断性**：小节/拍号/调号/声部靠猜；不应期望与原件一致。实测 canon 0.5 s，产物可被本工具回读（2 声部） |
| `midi → mp3` | P1 | ✅ FluidSynth 渲染 + ffmpeg 编码 | FluidSynth、ffmpeg | 陷阱：很多工具导出的 MIDI 会丢 Program，全部落到钢琴 —— 结果里会**给出警告**并提示先用「生成套件」页的音色映射表重写 MIDI |
| `midi → wav` | P2 | ✅ FluidSynth | FluidSynth | 实测 canon 4.8 s |
| `svg → musicxml` | P2 | ✅ **仅支持本工具生成的 SVG**：复用同目录/同套件保留的 MusicXML 侧车文件并规范化输出（SVG 结构本身不含完整记谱信息）；没有 `g.note` 结构或找不到侧车文件时**明确拒绝** | — | **不能**处理任意第三方 SVG（如 MuseScore 导出） |
| `svg → midi/mp3/pdf` | — | **不单独实现**：UI 引导 `svg → musicxml → midi/mp3/pdf` | — | 沿用 v1.0 的简化决策 |
| `pdf → svg` | P2 | ✅ 用 `pypdf` 提取 PDF 内嵌的 MusicXML，再交给 Verovio 重新渲染（保持矢量）；无 pypdf / 无内嵌乐谱时**明确拒绝**并提示 `pip install pypdf`（或 pypdfium2 位图方案，非矢量） | pypdf、verovio | 本机未装 pypdf，因此该路径目前只做"依赖自检 + 明确拒绝"，不产出位图降级结果 |
| `mp3 → musicxml` | **不做（P3 远期）** | 见 §7.4 | — | 需自动转谱（AMT），研究级难题 |
| `pdf → musicxml` | **不做（P3 远期）** | 见 §7.4 | — | 需 OMR 引擎（Audiveris 等），建议以外部工具桥接 |
| `mp3 / midi → wav`、`wav → mp3` | P2 | ✅ ffmpeg（`-codec:a pcm_s16le` / `libmp3lame -b:a 192k`） | ffmpeg | 实测 canon mp3→wav 0.1 s |

#### 5.4.2 界面需求

| 编号 | 需求 |
| :--- | :--- |
| **F3.1** | ✅ 已实现。源文件 / 源目录选择（可批量入列），目标文件夹选择，目标格式下拉（**按源格式动态过滤**；多格式混排时取交集；不可用组合置灰并给出原因 tooltip） |
| **F3.2** | ✅ 已实现。转换前"依赖自检"：逐项列出该转换需要的引擎（verovio / FluidSynth / ffmpeg / MuseScore / music21 / pypdf / 音色库），缺任一即**禁止开始**并给出安装提示，旁边就是「重新检测」与「打开设置」（配置入口） |
| **F3.3** | ✅ 已实现。转换前"保真度提示"：对 `musicxml → midi/mp3`、`midi → musicxml`、`svg → musicxml` 弹一次性提示（可勾选"不再提示"，写进 `config.ui.skip_fidelity_prompts`），说明会丢失什么 / 会推断什么 |
| **F3.4** | ✅ 已实现。批量转换：文件列表 + 每项状态（等待 / 进行中 / 成功 / 失败 / 跳过 / 已取消）+ 产物绝对路径 + 备注列 + 汇总行；单项失败不影响其它项 |
| **F3.5** | ✅ 已实现。全部长任务在 `QThread` 里执行（`ui/worker.BatchRunner`），UI 不冻结；支持协作式取消（**步骤之间**生效：MuseScore / FluidSynth / ffmpeg 这类外部进程无法中途打断，取消后当前文件会跑完） |
| **F3.6** | ✅ 已实现。结果区显示产物绝对路径，提供「打开所在文件夹」（Windows 下用 `explorer /select` 选中该文件）、「打开产物」与「载入播放界面」（产物是带音频的套件时才可用，见 §12.10） |
| **F3.7** | ✅ 已实现。命名规则：默认"源主名 + 新扩展名"，模板支持 `{base}` `{format}` `{ext}` `{date}`（如 `{base}_{format}`）；重名按 skip / overwrite / newdir 处理（转换没有目录概念，`newdir` 退化为"带序号的文件名 / 套件目录名"），模板里的路径分隔符会被中和成 `_` |
| **F3.8** | **源文件预览**（v2.6 新增）：源文件行「选择…」**左侧**有「预览」按钮；**没选文件时置灰**，选了 `MusicXML / SVG / MIDI / MP3` 才可用（其它格式置灰并说明原因）。点开弹**非模态**窗口：MusicXML / SVG → **显示**乐谱（SVG 直接渲染；MusicXML 用 Verovio 渲染前若干行，失败则退回显示源码文本）；MIDI / MP3 → **播放**（播放 / 停止按钮 + 进度条）。窗口按类型各复用一个实例 |

### 5.5 界面需求（整体 GUI）

| 编号 | 需求 |
| :--- | :--- |
| **F4.1** | 主窗口 = `QMainWindow` + 中心 `QTabWidget`，三个标签：`生成套件` / `同步播放` / `格式转换` |
| **F4.2** | 标签位置可配置（左 `west` / 上 `north` / 右 `east` / 下 `south`），默认**左侧**；设置持久化 |
| **F4.3** | 日志区：底部 `QDockWidget`（可折叠 / 浮动 / 隐藏），分级（INFO / WARN / ERROR / DEBUG），带颜色，可按级别过滤、复制、导出 `.log` |
| **F4.4** | 日志区必须显示：当前源文件名、目标文件夹、目标文件名、当前音色库、渲染引擎与版本、耗时、所有警告与错误 |
| **F4.5** | 状态栏常驻显示：当前套件 / 文件、音色库、引擎版本、任务进度条 |
| **F4.6** | 设置对话框：路径配置（§3.3）、默认目录、高亮颜色 / 透明度、标签位置、ffmpeg 参数（码率 / 采样率）、是否保留 WAV |
| **F4.7** | 窗口几何与分栏状态持久化（`QSettings`） |
| **F4.8** | 界面语言：中文（本期） |
| **F4.9** | 可用性：所有耗时操作有可见反馈；危险操作（覆盖文件）需确认 |

---

## 六、数据流与模块划分

```
┌──────────────────────── zpymusic ─────────────────────────┐
│ ui/        main_window, log_dock, settings_dialog,        │
│            tab_generate, tab_play, tab_convert            │
│ core/      musicxml_io.py    (.mxl 解包 / 规范化 / 校验)   │
│            score_render.py   (Verovio → SVG + timemap)    │
│            score_to_midi.py  (Verovio / music21)          │
│            audio_render.py   (FluidSynth → WAV)           │
│            audio_encode.py   (ffmpeg → MP3 / WAV)         │
│            midi_to_score.py  (music21 反向转换)            │
│            pdf_export.py     (MuseScore CLI)              │
│            suite.py          (路径 / 命名 / 发现 / 校验)    │
│            sync_model.py     (sync.json 读写 + 契约校验)    │
│ sync/      timeline.py       (二分查找 / 增量 diff)        │
│            player.py         (PlaybackBackend 抽象)        │
│            highlight.py      (注入 CSS / DOM 操作)         │
│            svg_view.py       (QWebEngineView 封装)         │
│ common/    config.py, paths.py, deps.py (外部工具探测),    │
│            log.py, tasks.py (QThread 任务与进度), errors.py │
└────────────────────────────────────────────────────────────┘
```

**核心数据模型**

```python
@dataclass
class NoteEvent:  id: str; onset_ms: int; dur_ms: int; part: str; staff: int
                  measure: int; midi_pitch: int; is_rest: bool
@dataclass
class PartInfo:   id: str; name: str; midi_program: int; midi_channel: int
@dataclass
class SyncDoc:    schema: str; source: dict; render: dict; audio: dict
                  parts: list[PartInfo]; tempo: dict; notes: list[NoteEvent]
@dataclass
class Suite:      root: Path; base: str
                  musicxml: Path; svg: list[Path]; midi: Path; mp3: Path; sync: Path
```

**模块边界原则**：`core/*` **不导入任何 Qt 类型**（可单测、可 CLI 复用）；`ui/*` 只调用 `core` 与 `sync` 的公开接口。**建议同时提供 `python -m zpymusic.cli` 命令行入口**，使转换能力可脚本化 / 批处理，也让单元测试不依赖 GUI。

---

## 七、非功能需求

### 7.1 性能指标（验收目标）

| 场景 | 指标 |
| :--- | :--- |
| `the-four-seasons-complete.mxl`（347 KB）→ SVG + MIDI + `sync.json` | ≤ 10 s |
| 单页 SVG（约 200 音符）渲染 | ≤ 1 s |
| `canon-in-d-easy` → MP3（39 MB sf3，44.1 kHz） | ≤ 30 s（明显快于实时） |
| 播放高亮延迟（相对音频） | Phase 1 ≤ 100 ms；Phase 2 目标 ≤ 30 ms |
| 高亮刷新率 | ≥ 30 fps（1000 音符单页不掉帧） |
| 峰值内存 | ≤ 1.5 GB（含 QWebEngine + 148 MB 音色库） |
| 启动时间（含引擎探测） | ≤ 3 s |
| 长曲目 | ≥ 30 分钟连续播放，`offset_ms` 校准后漂移 ≤ 200 ms |

### 7.2 日志与错误处理

- 统一 `logging`，输出到：GUI 日志区、`logs/zpymusic_YYYYMMDD.log`（滚动，保留 7 天）、stderr。
- 每类失败给**可操作**的提示，禁止只弹"失败"：
  - `文件不是有效的 MusicXML：未在 META-INF/container.xml 中找到 rootfile`
  - `未找到 FluidSynth 动态库：请将 libfluidsynth-x.y.z.dll 放入 <路径>，或在设置中指定`
  - `MuseScore 4 未找到，PDF 导出不可用（查找路径：…）`
- 任务失败 / 取消时删除半成品，避免套件目录出现"看似成功"的残缺套件。
- 提供套件完整性校验接口 `suite.validate()`，返回缺失项清单，供播放界面与"检查套件"按钮使用。

### 7.3 兼容性与健壮性

- MusicXML 1.0–4.0、`score-partwise` / `score-timewise`、`.xml` / `.musicxml` / `.mxl`、带 BOM / UTF-16。
- 非法或超常规输入不得崩溃：解析失败 → 报错退出；单页渲染超时 → 可跳过该页并警告。
- 路径含空格、中文、非 ASCII 字符必须可用（本机 MuseScore / ffmpeg 路径已含空格，属已验证场景）。

### 7.4 能力极限清单（回答 v1.0 第四章的全部疑问）

**Q：本工具能否对 MusicXML 完全正确解析和操作？极限在哪？**

**A：不能保证"完全正确"。** 极限来自三处，且都必须在 UI 中明示：

1. **解析极限**：Verovio / music21 都只支持 MusicXML 的一个**子集**（尤其 4.0 新特性、罕见写法、非标准扩展、`<other-notation>`）。做法：解析后统计"未识别元素 / 属性"并写入日志；对影响演奏的未识别项给出警告。
2. **演奏极限**：装饰音（grace）、反复记号（repeat / da capo / volta）、连音与颤音、踏板、摇摆（swing）等"记谱 ≠ 实际发声"的情形，各引擎展开规则不同。同一文件用 Verovio 与 MuseScore 播放，**听起来可以不一样**。做法：明确"以本工具渲染引擎的展开为准"，并把展开结果（timemap）随套件保存，保证套件自洽。
3. **转换极限**：正向降维、反向增维（见 §1.2 第 2 条）。

**Q：多乐器交响乐怎么办？正向 `musicxml → midi` 是否无法包含多乐器信息？反向能否分辨多种乐器？**

**A：正向可以，反向不能。**

- **正向 `musicxml → midi` 可以保留多乐器信息**（纠正 v1.0 的猜测）：MusicXML 的 `score-part` 天然对应 MIDI 的 track / channel，`part-list/score-part/midi-instrument` 中的 `midi-channel` / `midi-program` 就是为此设计的，MIDI 格式 1 支持多轨。真正的限制是：
  - **16 个 MIDI 通道**（通道 10 固定为打击乐），因此 **>15 个同时发声的乐器组**需要通道复用 / 声部归并 → 本工具按 GM 家族归并，并在日志中列出被归并的声部。
  - **源文件未写 `midi-program`** 时只能推断（默认钢琴），这正是"根据乐谱自动选多种乐器音色"的难点 → 本工具默认用文件内的 `midi-program`；缺失时按乐器名做一次 GM 名称模糊匹配，结果放进 §F1.8 的可编辑表格让用户确认，**不静默猜测**。
  - **打击乐**需处理 `midi-unpitched` 映射与非标准谱线位置。
- **反向 `midi → musicxml` 无法分辨原始乐器摆放**：MIDI 只保留 channel 与 program，能推出"通道 3 是 Violin（GM 40）"，但**推不出**它在原谱里是第几声部、用了什么谱号、如何分谱布局、原名叫什么。因此反向结果永远是"按通道重排的新乐谱"。**但对 `midi → mp3` 足够**：读通道 + program 即可正确选音色。
- **多乐器 `musicxml → mp3` 的正确做法**：不要"先降级成单音色再播"。链路是 `MusicXML --(Verovio)--> 多轨 MIDI --(FluidSynth 按通道 program 加载 SoundFont)--> 多乐器 MP3`。FluidSynth 本身按通道 program 选预设，**只要 MIDI 里的 program 正确，多乐器 MP3 就自动正确**；本工具要做的是让 F1.8 的映射表可审阅、可覆盖。

**Q：`mp3 → musicxml` 是否有难度、有无必要？如何把混合音乐拆成各个乐器并识别乐器？**

- **有难度，且本期不做。** 这实际包含两个子问题：
  1. **多乐器分轨（源分离）**：可用 Demucs 等模型分离出若干"声部"，但分离结果是音频，不是记谱；
  2. **自动转谱（AMT）**：单声部简单旋律尚可（Basic Pitch 等），复调钢琴 / 乐队**准确率不足**，产出会误导用户。
- 结论：列为 P3 远期。若要做，先做"**单声部旋律 → musicxml**"并标注实验性；乐器识别（instrument classification）可作为独立小功能（对单音轨音频做分类）先行提供。

**Q：`pdf → musicxml` 出错的原因主要是光学识别吗？还有别的干扰因素吗？**

- 主要是，但不只。分层结论：
  1. **PDF 内嵌 MusicXML 附件** → 直接提取（`pypdf` 可读 embedded files），**零 OCR、零误差**，优先实现；
  2. **由 MuseScore / Verovio 等本工具链生成的 PDF** → 理论上可读 PDF 文本与结构元数据（`/Producer`、嵌入字体、自定义键）重建，但工作量大、收益低，**不建议**；
  3. **扫描件 / 印刷 PDF** → 必须 OMR（Audiveris、PhotoScore 等）。
- 即便走 OMR，干扰因素也不止"识别率"：
  - 图像质量（倾斜、噪点、装订阴影、双页扫描未切分）；
  - **符号歧义**：符干方向、连音线与圆滑线难分、小节线 vs 反复记号、装饰音的归属；
  - **语义缺失**：PDF 无"声部 / 层"概念，多声部交错时无法确定归属；调号与前缀记号需推断；
  - **排版信息被误当语义**（如跨行断开的连音线、跨页小节）；
  - 多页拼接与页面顺序、不同版本的记谱约定差异。
- 建议：**以"外部工具桥接"方式支持**（用户配置 Audiveris 路径，本工具调用并导入其结果），而不是自研 OMR。

---

## 八、测试与验收

### 8.1 样本集

| 样本 | 覆盖点 |
| :--- | :--- |
| `canon-in-d-easy.mxl` | 基础单声部、连音、反复 |
| `bach-minuet-in-g-major-bwv-anh-116.mxl` | 双手钢琴、双谱表 |
| `waltz-in-a-minorchopin.mxl` | 装饰音（grace）、踏板、三拍子 |
| `maple-leaf-rag-scott-joplin.mxl`（21 KB）、`i-wanna-be-like-you-ragtime-*.mxl`（27 KB） | 切分、密集和弦 |
| `rachmaninoff-...-solo-piano.mxl`（24 KB） | 复杂钢琴织体、多声部单谱表 |
| `bach-cello-suite-no-1-for-violin.mxl` | 单旋律；容器根文件名为 `lg-*.xml` |
| `vivaldi-violin-concerto-...-for-solo-piano.mxl`（77 KB） | 较长、中等复杂度 |
| `the-four-seasons-complete.mxl`（347 KB） | **压力样本**：多乐章、多声部 |
| `tango-la-cumparsita-...parte-a.mxl` | 混合记谱风格 |
| 自造样本 | 多乐器交响乐（≥16 通道）、打击乐通道 10、明文 `.musicxml`、UTF-16、损坏 ZIP |

### 8.2 验收标准（P0）与实测结论

| # | 标准 | 状态 | 实测证据 |
| :--- | :--- | :--- | :--- |
| 1 | 全部样本 `musicxml → {svg, midi, mp3, sync.json}` 无崩溃，失败项有明确原因 | ✅ | 10/10 成功（`zpymusic generate`） |
| 2 | 每个 `sync.json.notes[].id` 都能在 SVG 中找到（**100%**） | ✅ | 43281 个事件全部命中（`tools/verify_suites.py`） |
| 3 | 播放时高亮与音频对齐（自动化：`sync.json` 事件起点 vs MIDI note-on，偏差 ≤ 100 ms） | ✅ | 10/10 套件 **100%** 命中（同上） |
| 4 | 速率 50% / 100% / 200% 下高亮不漂移 | ✅ | `tools/smoke_play.py` 校验"变速后乐谱时间连续"；`tests/test_timeline.py` 逐档位校验 |
| 5 | 缺 SVG / 缺 sync.json / 缺 FluidSynth / 缺 MuseScore 的降级路径有提示且不崩溃 | ✅ | `Suite.validate()`、`controller.load_suite()` 的降级分支、`deps.probe_all()` 警告 |
| 6 | `midi → musicxml` 可往返 | ⏳ | M5 实现（music21） |

补充的实测指标（M4 新增）：

| 指标 | 结果 |
| :--- | :--- |
| 单 system 几何解析 | 9–22 ms（canon 9 ms / four-seasons 22 ms） |
| 载入 276 个 system 的套件（仅登记，不挂载 SVG） | **79 ms** |
| 首屏挂载 | 0.1 ms（只挂载视口附近的 3–6 行） |
| 连续滚动挂载 60 行 | 1.17 s（约 20 ms/行） |
| 符头几何精度 | 9.08 × 7.7 px（与 5 线谱表高 28.8 px 的比例正确） |
| M4 冒烟测试 | 28 项检查 × 3 个套件（含 276 行的 four-seasons）全部通过 |

### 8.3 测试策略

- `core/*` 与 `sync/*` 全部可无 GUI 单测（pytest）；样本文件入库（`tests/data/` 放 2–3 个小样本）。
- GUI 冒烟测试：`pytest-qt`（可选）。
- 回归基线：固定样本的产物哈希 + 关键统计（音符数、总时长），防止升级 Verovio / 依赖后静默变化。

---

## 九、风险与对策

| 编号 | 风险 | 影响 | 对策 |
| :--- | :--- | :--- | :--- |
| **R1** | FluidSynth 动态库在 Windows 上需手动放置，`pyfluidsynth` 找不到库 | **阻断** `midi → mp3` | 启动探测 + 明确的设置入口 + 文档给出下载与放置步骤；探测失败时禁用相关功能而非崩溃 |
| **R2** | Verovio 的 MusicXML 导入子集覆盖不足，某些样本渲染异常 | 高 | 双引擎策略：Verovio 失败时可回退 MuseScore 4 导出 SVG（但**无音符 ID → 无同步**，需明示降级）；"渲染引擎"做成可选项并记入 `sync.json` |
| **R3** | Verovio 的 MIDI 导出与 MuseScore 听感不同（装饰音 / 反复展开规则） | 中 | 以"同一引擎同源"为准（D2 / D3）；在日志与 `sync.json` 记录引擎与版本；提供"改用 MuseScore 渲染音频"的实验选项（此时同步仍用 Verovio timemap，需接受微小偏差） |
| **R4** | `QWebEngineView` 体积 / 环境限制（部分环境禁用 GPU、打包体积大） | 中 | 把 SVG 视图抽象为 `ScoreView` 接口，保留 `QGraphicsSvgItem` 降级实现；打包时附 `QtWebEngineProcess` 必备文件清单 |
| **R5** | `QMediaPlayer` 位置精度不足导致高亮抖动 / 漂移 | 中 | Phase 1 接受 ≤100 ms 并提供 `offset_ms` 校准；Phase 2 换 `sounddevice`（D6） |
| **R6** | 大文件（交响乐）渲染内存 / 耗时失控 | 中 | 分页渲染 + 进度 + 取消；单文件 ≥50 MB 或 ≥200 页时先警告 |
| **R7** | 依赖版本升级导致产物变化 / 契约破坏 | 中 | `sync.json` 带 `schema` 与引擎版本；`schema` 变更走版本号；维护回归基线（§8.3） |
| **R8** | 反向转换结果被用户误认为"原件" | 中 | UI 强制保真度提示（F3.3）；产物名可选加 `_from_midi` 后缀；日志记录推断参数（量化网格等） |
| **R9** | SoundFont 授权 | 低 | 默认只用 MIT / 明确授权的库；`FluidR3_GM2-2.SF2` 等需在设置中标注授权与来源 |

---

## 十、实施计划

### 10.1 阶段划分（沿用 v1.0，细化为里程碑）

| 阶段 | 内容 | 产出 |
| :--- | :--- | :--- |
| **M0 环境与骨架** | 依赖安装（verovio / music21 / pyfluidsynth）、外部工具探测、配置、日志、主窗口 + 三标签 + 日志 Dock | 可启动的空壳 + 环境自检日志 |
| **M1 正向转换核心** | `musicxml_io`、`score_render`、`score_to_midi`、`sync.json`、`suite` | CLI 跑通"样本 → 套件"，产物可人工验证 |
| **M2 生成套件界面**（功能性 3.1） | F1.*、进度 / 取消 / 批量 / 覆盖策略、F1.8 音色映射表 | 界面一可用 |
| **M3 音频渲染** | FluidSynth（含 R1 探测与降级）、ffmpeg 编码、F1.6 | 套件含 MP3 |
| **M4 同步播放**（功能性 3.2） | `svg_view` + `timeline` + `player` + `highlight`、F2.* | 核心亮点功能可用 |
| **M5 转换界面与其余格式**（功能性 3.3） | F3.*、`pdf`（MuseScore）、`midi → musicxml`、`svg → musicxml`、`pdf → svg`、批量 | 界面三可用 |
| **M6 打磨与交付** | 性能（Phase 2 换 `sounddevice`）、打包、文档、回归基线 | 可交付版本 |

### 10.2 与 v1.0 实施计划的对齐说明

1. **第一阶段 = M1–M4**：先打通 3.1 → 用 3.2 验证 3.1，与 v1.0 一致。**建议把 M1（无界面的核心链路 + CLI）单列**，这样 3.1 的正确性可以在做界面之前用命令行与单测锁死，避免界面调试掩盖数据问题。
2. **第二阶段 = M5**：3.3 的小功能点较多，**按 §5.4.1 的 P0/P1/P2 优先级分批交付**。无法克服或无必要的（§5.4.1 中标 P3 的 `mp3 → musicxml`、`pdf → musicxml`，以及"第三方 SVG → musicxml"）**明确延后或取消**，只保留 UI 引导与外部工具桥接。
3. **第三阶段 = M6**：测试与打包。补充：打包需处理 QtWebEngine 与 ffmpeg / MuseScore 的外部依赖（后者不打包，改为配置路径）。

---

## 十一、待确认事项（需要你决策）

| 编号 | 问题 | 建议 / 默认值 |
| :--- | :--- | :--- |
| **Q1** | 播放速度调节：用"播放速率 50%–200%"（实时生效）还是"真实 BPM 重渲染"（每次调节需重新渲染，慢）？ | **默认速率调节**；真实 BPM 列为 Phase 2 |
| **Q2** | SVG 版面：**单页连续纵向**（推荐，滚动跟随简单）还是**按纸张分页**（便于导出 A4 打印）？ | **单页连续**为默认，分页作为导出 PDF 时的选项 |
| **Q3** | 是否同时保留 WAV？ | 默认**不保留**（仅中间产物），设置中可开启 |
| **Q4** | MP3 参数默认值 | 44.1 kHz / 192 kbps / 立体声 |
| **Q5** | 默认音色库 | `MuseScore_General.sf3`（MIT，体积 / 质量平衡） |
| **Q6** | 接受引入 `QWebEngineView`（体积大但高亮 / 交互最好）？ | **接受**（已在 `PySide6-Addons` 中，无新增依赖） |
| **Q7** | 是否需要命令行入口 `python -m zpymusic.cli`？ | **建议需要**（便于批处理与测试） |
| **Q8** | 是否需要"分谱导出"（每个声部单独一套 SVG / MIDI / MP3）？ | Phase 2 可选；对交响乐场景价值高 |
| **Q9** | 反复记号与装饰音的展开是否允许用户配置（展开 / 不展开）？ | 默认"按引擎默认展开"，先不暴露配置 |
| **Q10** | `.mxl` 与规范化 `.musicxml` 是否都留在套件里？ | **都留**（便于溯源与兼容第三方工具） |

---

## 十二、实现现状（M0–M2，2026-02）

代码位于 `zpymusic/src/zpymusic/`，命令行入口 `python -m zpymusic`。

### 12.1 已实现

| 需求 | 状态 | 实现位置 |
| :--- | :--- | :--- |
| M0 依赖探测（verovio / FluidSynth / ffmpeg / MuseScore / 音色库） | ✅ | `common/deps.py`、`zpymusic probe` |
| M0 配置（`config.json`，含未知键容忍） | ✅ | `common/config.py` |
| M0 日志（GUI + 滚动文件 + stderr） | ✅ | `common/log.py`、`ui/log_dock.py` |
| M0 主窗口 + 三标签 + 日志 Dock + 状态栏 + 设置对话框 | ✅ | `ui/main_window.py`、`ui/settings_dialog.py` |
| F1.1–F1.2 源/目标选择、批量、覆盖策略 | ✅ | `ui/tab_generate.py`、`core/suite.py` |
| F1.3 SVG（音符 ID + 每 system 一个文件） | ✅ | `core/score_render.py` |
| F1.4 MIDI（与时间轴同源） | ✅ | `core/score_render.py` |
| F1.5 `sync.json`（含一致性自检） | ✅ | `core/sync_model.py`、`core/pipeline.py` |
| F1.6 MP3（FluidSynth → ffmpeg，可选保留 WAV） | ✅ | `core/audio_render.py` |
| F1.7 复制源 MusicXML + 保留 `.mxl` | ✅ | `core/suite.py` |
| F1.8 音色映射表（可编辑、标注来源、通道冲突提示） | ✅ | `ui/parts_table.py`、`core/gm.py`、`core/midi_tools.py` |
| F1.9 进度 / 取消 / 后台线程 | ✅ | `ui/worker.py` |
| F1.10 跳过 / 覆盖 / 新建带序号目录 | ✅ | `core/suite.py` |
| §5.4.1 `musicxml → svg/midi/mp3/wav` | ✅（复用流水线） | `cli.py::cmd_convert` |
| §7.2 套件完整性校验 | ✅ | `core/suite.py::Suite.validate`、`zpymusic validate` |
| Q7 命令行入口 | ✅ | `cli.py` |
| 时间轴与增量高亮算法（F2.6 的基础） | ✅ | `sync/timeline.py` |
| F2.1–F2.9 同步播放全部功能 | ✅ | `ui/tab_play.py`、`sync/controller.py`、`sync/player.py`、`sync/score_view.py`、`sync/highlight.py` |
| 曲谱几何解析（高亮框 / 点击命中） | ✅ | `sync/svg_geometry.py` |
| WebEngine 后端（DOM 高亮）+ 回退机制 | ✅ | `sync/web_view.py`、`sync/local_server.py` |
| F3.1 按源格式动态过滤 + F3.3 保真度提示（只读矩阵） | ✅ | `ui/tab_convert.py` |
| **F3.1–F3.7 转换执行 / 依赖自检 / 批量 / 结果区 / 命名模板** | ✅（M5 完成） | `core/convert.py`、`ui/tab_convert.py`、`ui/open_utils.py` |
| **F3.8 源文件预览**（v2.6 新增） | ✅ | `ui/tab_convert.py`、`ui/preview.py`、`core/score_render.py` |

### 12.2 待实现（后续里程碑）

| 需求 | 计划 |
| :--- | :--- |
| F2.10 从 SVG 属性重建时间轴（无 sync.json 时的兜底） | 待定（当前已有"只播放不高亮"的降级） |
| `svg → midi/mp3/pdf` | **不单独实现**：UI 引导 `svg → musicxml → midi/mp3/pdf`（§5.4.1） |
| `mp3 → musicxml`、`pdf → musicxml`、第三方 SVG → musicxml | **不做**（P3，见 §7.4） |
| `pdf → svg` 的位图降级（pypdfium2） | 可选：装上 pypdfium2 后可补，属于"非矢量、不保证可缩放"的降级方案 |

### 12.3 M0–M2 的实测修正（对 v2.0 的改动）

1. **`getTimesForElement()` 不能用**：本机 verovio 6.3.0 对绝大多数 ID 返回全 0，
   且 29324 个 ID 逐个调用耗时 132 s。改用 `renderToTimemap()` 的 `on`/`off` 配对，
   全曲 0.17 s，并用 `getElementsAtTime()` 交叉验证一致。（§4 D2、附录 B）
2. **SVG 版面**：从"单页连续"改为"一行一个紧凑 SVG"，并在套件内放入 `svg/` 子目录。（§5.1.2）
3. **连音幽灵音符**：用 `getNotatedIdForElement()` + 区间重叠合并解决。（§5.1.4）
4. **`sync.json` 契约升级**：`schema` → `zpymusic-sync/1.1`，`notes` 用紧凑键，
   新增 `systems` / `measures`。（§5.1.4）
5. **FluidSynth 渲染方式**：`pyfluidsynth` 1.4.0 在 Windows 上既找不到 DLL，
   也没有 `Sequencer.play_midi_file`；改用 `fluidsynth.exe -a file` 离线渲染，
   实测 **27–28× 实时**（canon 130 s 音频 4.8 s 完成）。（§3.3、§5.2 F1.6）
6. **音频时长校验阈值放宽**：渲染音频比乐谱长是**正常**的（混响/延音尾巴，
   实测 tango 多出 3.1 s）；真正该校验的是 **MIDI 时长 vs `sync.json` 时间轴**
   （实测 10 个样本误差均 < 10 ms），已作为流水线的固定自检项。

### 12.4 已知问题

| 编号 | 问题 | 影响 | 处理 |
| :--- | :--- | :--- | :--- |
| **K1** | 本机 MuseScore 4 加载 MusicXML 时**不读** `midi-channel` / `midi-program` | 若用 MuseScore 链渲染多乐器音频，声部音色需要用户手动指定 | 当前音频链走 Verovio + FluidSynth，不受影响；M5 的 PDF 导出只涉及排版，也不受影响 |
| **K2** | `import fluidsynth` 会因找不到 DLL 失败（即使 DLL 已在 `tools/fluidsynth`） | 直接用 `pyfluidsynth` 的代码会 ImportError | `common/deps.prepare_fluidsynth()` 负责加入搜索路径；音频渲染实际走 CLI，不依赖它 |
| **K3** | 受沙箱限制，`pip` 解包元数据、系统 TEMP 写入、写 `C:\ffmpeg` 会被拒绝 | 只影响本机的安装/测试环境，不影响产品逻辑 | 测试用工作区内的 `tmp_path`（见 `tests/conftest.py`）；FluidSynth 装在项目内 `tools/fluidsynth` |
| **K4** | Verovio 对个别样本报 `There are 1 ties left open` / `tie ... could not be set as running` | 该处连音的时值可能略有偏差 | 属于引擎对不规范连音的处理；已归并区间，不产生幽灵音符，仅记入警告 |
| **K5** | 在**工作线程**里探测依赖（会启动子进程）与主线程创建 `QSvgRenderer` 并发时，进程以 `access violation` 崩溃 | 启动即闪退（无 Python 异常可捕获） | 探测改为**同步执行**并去掉全部子进程调用（`common/deps._tool_version` 改读安装目录名）；渲染器只在主线程创建（M4 实测定位并修复） |
| **K6** | 受限沙箱 / 受管环境下 QtWebEngine 无法启动（`FATAL:named-platform-channel-pipe ... 拒绝访问`），且是**致命 abort**、无法用 try/except 捕获 | 该环境里 WebEngine 后端不可用（与显卡无关） | `sync/web_view.is_webengine_available()` 用**子进程**探测后自动回退到原生 QtSvg 后端；普通桌面环境不受影响；可用 `ZPYMUSIC_WEBENGINE=1/0` 强制指定。失败原因会分类记录（管道 / GL / 超时 / 其它），见 §12.11 |
| **K7** | `QSvgRenderer.boundsOnElement()` / `QGraphicsSvgItem.setElementId()` 在 Qt6 下不可用（对任意 ID 返回整个 viewport，`elementExists()` 恒为 False） | 无法用 Qt 自带 API 取元素几何 | 自研 `sync/svg_geometry.parse_svg()`；高亮改用半透明覆盖框而非给元素换色 |
| **K8** | QtSvg **无法渲染 Verovio 的嵌套 `<svg>`**，且失败方式隐蔽：`isValid()==True`、`defaultSize()` 正确，但渲染结果全白 | 原生后端一开始显示空白谱面 | `sync/svg_geometry.flatten_svg()` 把内层 `<svg>` 降级为 `<g>` 并把 `viewBox` 提到根元素（实测深色像素 0 → 4501） |

### 12.5 M4 的实测修正（对 v2.1 的改动）

M4 做同步播放时，为了把高亮框画在正确的音符上，必须拿到"元素几何"，
这一路上暴露了 6 个此前看不出来的问题：

1. **`scale=40` + `pageHeight=400` 让整份谱缩小约 5 倍（M1 遗留）**
   M1 选 `scale=40` 时只看"页数"，没检查"字有多大"。M4 解析几何才发现：
   canon 一个 system 只有 188 px 高、符头只有 9.08×7.7 px，在 1280 宽的窗口里小到看不清。
   **修正**：`scale` 提到 **100**、`page_height` 提到 **1500**，并把
   `pageWidth` 真正用起来（2100 px 宽的页面，符头 22.7 px，字可读）。
2. **`adjustPageHeight` 曾被我误判为有害（v2.3 更正）**
   M4 中期我写的是"它与 `breaks=smart` 同时开启会把每行压扁"，于是关掉了它。
   后续处理"**某些曲谱界面横向很宽、页面下方大量留白**"时重新做了对照实验，
   发现**那个结论是错的** —— 当时真正的元凶是 `scale=40` + `pageHeight=400`
   共同作用（页高放不下一个 system，Verovio 只能整体缩放）。
   在 `scale=100` 下实测：

   | 配置 | rachmaninoff | canon | 符头宽 |
   | :--- | :--- | :--- | :--- |
   | `pageHeight=1500, adjustPageHeight=False` | 2100×1500 | 2100×1500（空白 390 px） | 22.7 px |
   | **`pageHeight=1500, adjustPageHeight=True`** | **2100×1439** | **2100×1260** | **22.7 px** |

   即：开启后**高度贴合内容、字号完全不变**。故 **v2.3 起默认开启**。
2. **Verovio 的 SVG 是两层嵌套，单位换算由内层 viewBox 决定**
   `svg_geometry` 第一版按"100 单位 = 1 px"猜，算出的符头只有 2.3 px。
   实际结构是外层 `width="840px"`、内层 `<svg class="definition-scale" viewBox="0 0 21000 4680">`，
   换算是 **840 / 21000 = 0.04**。现在由 `_inner_viewbox_scale()` 从文件里读出，不再硬编码。
3. **`<use>` 必须解引用到 `<defs>` 里的字形**
   Verovio 把符头放在 `<defs>` 里用 `<use xlink:href="#E0A4-…" transform="translate(4857,2150) scale(0.72,0.72)"/>`
   引用。只看 `<use>` 只能得到一个点（宽高为 0），必须解引用字形才能得到真实尺寸。
4. **QtSvg 无法渲染 Verovio 的嵌套 `<svg>`，且失败方式极其隐蔽**
   `QSvgRenderer` 对原始文件报告 `isValid()==True`、`defaultSize()==840×188`，
   但**渲染出来是全白**（深色像素 0 个）。把内层 `<svg>` 降级为 `<g>`、`viewBox` 提到根元素后，
   同一个 QtSvg 立刻能画出谱（深色像素 4501 个）。这个转换就是
   `sync/svg_geometry.flatten_svg()`，**必须用文本替换实现**——用 `ET.tostring()` 重写会引入
   `ns0:` 前缀与自闭合标签，QtSvg 会拒绝解析。
5. **线程竞争会让 QtSvg 崩溃（access violation）**
   M0 的"后台线程探测外部依赖"会启动子进程（MuseScore / ffmpeg 的 `--version`），
   而主线程同时创建 `QSvgRenderer` → QtSvg 内部状态被并发访问，进程直接 `0xC0000005` 退出。
   **修正**：探测改为**同步**执行、且彻底去掉子进程调用（版本号改从安装目录名读取），
   探测成本从数百毫秒降到约 10 ms。详见 §12.4 的 K5。
6. **`QMediaPlayer` 的两个 API 陷阱**
   `positionChanged` 携带 `qlonglong`，不能直接连到自定义 `Signal(int)`（PySide6 报
   `Failed to connect signal`），必须经普通方法转发；
   `QGraphicsSvgItem` 只在 C++ 侧持有 `setSharedRenderer()` 传入的渲染器，
   Python 包装对象一旦被 GC，item 内部就留下悬垂指针（实测崩溃）。
   现用 `item.setData(0, renderer)` 让 item 自己持有引用。

另外两个工程决定：

* **WebEngine 后端的可用性必须用子进程探测**。Chromium 初始化失败是**致命 abort**，
  没有 Python 异常可捕获；受限沙箱下会打印
  `FATAL:named-platform-channel-pipe(89) Check failed: ... 拒绝访问` 并终止进程。
  因此 `sync/web_view.is_webengine_available()` 在子进程里试（结果缓存），
  主进程再决定用哪个后端。可用环境变量 `ZPYMUSIC_WEBENGINE=1/0` 强制覆盖。
  **M6 起改为两段式**：先用默认（GPU 加速）配置探测，只有失败原因是"建不出 GL 上下文"
  时才用软件渲染兜底重试（见 §12.11）。
* **高亮在原生后端用"半透明覆盖框"，而不是给音符换色**。QtSvg 只能整幅渲染，
  `boundsOnElement()` 在 Qt6 下对任意 ID 都返回整个 viewport，无法单独改某个元素的颜色。
  覆盖框是纯增量绘制、不破坏谱面内容，且与 WebEngine 后端的观感一致
  （同一个 `HighlightPlan` 驱动两种后端）。

### 12.6 M4 新增模块

| 模块 | 职责 |
| :--- | :--- |
| `sync/svg_geometry.py` | 解析 Verovio SVG 的每个元素包围盒（含 `<use>` 解引用与 viewBox 换算）、`flatten_svg()` |
| `sync/highlight.py` | `SystemGeometryIndex`：按 system 索引几何、生成高亮计划、点击命中测试；`build_css()` 生成注入样式 |
| `sync/score_view.py` | `ScoreView` 抽象 + `NativeScoreView`（QtSvg + 覆盖框 + 按行懒加载 + 自动跟随） |
| `sync/web_view.py` | `WebScoreView`（QWebEngineView + DOM class 高亮）+ 子进程可用性探测 |
| `sync/local_server.py` | 只读本地 HTTP 服务（限 127.0.0.1、限套件目录、支持 Range），给 WebEngine 提供 SVG/音频 |
| `sync/player.py` | `PlaybackBackend` 抽象 + `QtMediaPlayer`（QMediaPlayer/QAudioOutput，速率 0.5–2.0） |
| `sync/controller.py` | `PlaybackController`：30 fps tick、变速锚点换算、seek/预览/校准、驱动视图高亮 |

### 12.7 M6 的三项用户反馈修复（对 v2.4 的改动）

用户实机试用后报了 3 个问题，全部定位到根因并修复（回归测试见
`tests/test_m6_view_fixes.py`，10 项）：

1. **"点播放后谱面上移一小段，右侧滚动条往下走一小段，而且回不到最顶端"**
   根因是跟随滚动的实现：`NativeScoreView.set_highlight()` 每帧都调用
   `scroll_to_system()` → `centerOn(当前行中心)`。于是一方面起播瞬间第一行被强行居中
   （实测 950×430 视口下滚动条从 0 跳到 61），另一方面播放中鼠标滚轮/拖动每动一点，
   就被下一帧的 `centerOn` 拉回去 —— 60 fps 的高亮刷新让用户"怎么都回不到顶端"。
   **修正**：改为"只在必要时滚动"（`_follow_highlight()`）——
   高亮音符仍在视口上下 12% 的舒适区内就**完全不动**（用户手动滚动优先）；
   真的跑出视口时才滚动一次，并把**当前行的顶端**对齐到视口 25% 处，
   保证每行阅读位置一致。WebEngine 后端同步改为页面里的 `zpy.followTo()`
   （原来是 `scrollIntoView({block:"center"})`，有同样的问题）。
2. **"适应宽度面板太靠右，挤占了滚动条"**
   缩放工具条是浮在曲谱视图上的，按"贴右边缘 + 10 px 边距"定位，
   而 Fusion 下纵向滚动条宽 14 px —— 实测压住滚动条顶端 4×26 px。
   **修正**：新增 `ScoreView.overlay_right_inset()`（原生后端返回滚动条宽度、
   WebEngine 返回页面滚动条的估计值 16 px），`ZoomBar.reposition()` 据此让开。
3. **"信息显示区是浅蓝色，看不清"**
   日志区下方那行上下文文本在**浅色**底上用了 `#9cdcfe`（VS Code 深色主题的浅蓝，
   亮度 0.78）。**修正**：改为深蓝 `#0b3d91` + 浅底描边条（亮度 0.22）、允许换行与选中。
   同一缺陷在「格式转换」优先级 P2、「声部表」已修改列的配色里也存在，一并改成 `#1565c0`。
4. **"窗口左下角一直显示'未选择套件'"**
   `MainWindow.lbl_current` 只在"生成套件"成功时写过一次，播放页切换套件从不更新
   （`PlayTab.load_suite_dir()` 甚至是**从未被调用**的死代码）。
   **修正**：`PlayTab` 新增 `suite_selected` 信号，主窗口据此刷新
   "当前套件 / 音色库"（含 tooltip 与套件 `sync.json` 里记录的音色库）；
   `_on_suite_created()` 改调 `load_suite_dir()` 真正切到刚生成的套件。

顺带修掉一个**会导致进程崩溃**的隐患：`NativeScoreView._mount()` 过去可以被
重复调用（旧 `QGraphicsSvgItem` 留在场景里，其 renderer 的 Python 引用被顶掉后 GC
→ 悬垂指针）。实测对同一个 system 调两次 `_mount()`，下一次重绘即 access violation；
现已改为幂等（已在 `_items` 里就直接返回）。

5. **"切到同步播放页，第一首曲谱不是适应宽度，换一首才正常"**
   `NativeScoreView.fit_width()` 的输入是 `self._view.viewport().width()`。
   而 Qt 的布局链里，父控件的 `resizeEvent` **先**跑，内层 `QGraphicsView` 的视口尺寸
   要等它自己处理完 resize 才更新。播放页是隐藏页，`PlayTab.__init__` 里就以默认
   640×480 完成了一次 `load()`；等用户切过去、真正被布局成 906×444 时，
   `viewport().width()` 还是 **626**（实测 `view.width()` 已经是 906），
   于是缩放算成 0.29，还被 `MIN_ZOOM=0.3` 夹住 —— 表现为"首屏 30%，换一首才 42%"。
   **修正**：改用**视图控件宽度** `self._view.width()` 计算可用宽度
   （顺便消除"缩放↔滚动条↔viewport 宽度"的反馈环），并新增
   `_maybe_fit_width()` 在**视图尺寸变化 / 视口尺寸变化 / 首次显示**三处自动补齐
   （尺寸没变则是空操作）。同时新增 `zoom_changed` 信号，让缩放工具条的百分比
   跟着自动适配更新（否则数字停在 30%）。顺带修掉 `fit_width()` 的返回类型谎报
   （`-> float` 实际返回 `None`，因为 `set_zoom()` 没有返回值）。

### 12.8 M7：播放高亮改为"跟随当前小节"（对 v2.4 的改动）

**用户反馈**：播放时高亮跟着**每一个音符**走（一个音符一个框，逐音符闪烁）。
**改为**：高亮**当前播放的小节** —— 高亮区域宽度 = 小节宽度、高度 ≈ 该行谱表高度。
回归测试见 `tests/test_measure_highlight.py`（31 项）。

**为什么可行（关键实测）**：Verovio 输出的 SVG 里，小节容器的 `id` 就是 MEI 的
小节 `xml:id`，与 `sync.json.measures[].id` **完全一致**：

```xml
<g id="imhghxi" class="measure">      <!-- sync.json: {"id":"imhghxi","onset_ms":0} -->
  <g id="r135ugis" class="staff">
    <path .../>                        <!-- 5 条谱线：给出小节的"宽度" -->
    <g id="c1hg30hn" class="clef">…
    <g class="layer"><g class="mRest">…
  <g id="q1nauwgi" class="barLine"><path .../>   <!-- 终止小节线：右边界 + 谱表高度 -->
```

因此这个小节的矩形 = **子树内所有形状的并集**，天然就是"小节宽度 × 谱表高度"，
不需要自己拼小节线、也不需要猜行高。实测 canon（两谱表钢琴谱）：
小节矩形高 276–318 px，而一行谱表的行距是 396 px —— 正是"大致与五线谱高度相当"。

**实现要点**：

| 位置 | 改动 |
| :--- | :--- |
| `sync/svg_geometry.py` | `parse_svg()` 在遍历时**顺带**把每个形状的包围盒并入所属小节容器（边走边并，不再为小节单独遍历一次子树 → 解析耗时与改动前持平：16 ms/system）。新增 `SvgGeometry.measure_box()` / `.measures` |
| `sync/timeline.py` | 新增 `Timeline.measure_of`（元素 → 小节）与 `measures_of()`（一行里依次出现的小节），供"小节 → 行"映射 |
| `sync/score_view.py` | `SystemRef.measure_ids`；`measure_systems()` 由 `sync.json` 回答（**不解析 SVG**，所以未挂载的行也能定位），并对 `-rendN` 变体双向容忍；`_index_measures()` 在挂载时把"行内实际出现的小节"补进映射（整小节只有休止符的情况靠它）；`_resolve_measure_boxes()` 按需挂载目标行并补全矩形；取不到几何时退回逐音符 |
| `sync/highlight.py` | `HighlightPlan.measure_id`（非空 = 小节模式，`rects[].box` 为空表示待视图补全）；`SystemGeometryIndex.measure_plan()` / `.measure_box()`；`build_css()` 增加小节矩形样式；命中测试**排除小节容器**（否则点在谱表空白处会返回小节 ID，点击跳转失效） |
| `sync/controller.py` | `_apply_highlight()` 每帧只判断"小节是否变了"，变了才重画一次；音符级 `HighlightTracker` 仍逐帧更新（状态栏"当前音符"与 F2.8"按当前音符校准"要用），但不再驱动视觉高亮。无小节数据 / 定位失败时自动退回逐音符 |
| `sync/web_view.py`、`sync/local_server.py` | WebEngine 后端把小节 ID 交给页面，页面用 `getBBox()` 取矩形并把 `<rect class="zpy-hl-measure">` 插进该小节容器（坐标换算交给浏览器）；找不到该小节时页面内自动回退到逐音符高亮 |
| `tools/check_page_js.js` | **页面 JS 的行为测试**：本机 WebEngine 跑不起来（Chromium 需要命名管道，直接 abort），于是用一个十几行的 DOM stub 在 node 里真跑一遍 `setMeasure` / `clearMeasure` / 回退逻辑（矩形几何、不累积、找不到小节时的回退、跟随滚动）。由 `tests/test_measure_highlight.py` 调用（本机没有 node 时自动跳过） |

**实测数据（10 个样本套件，`staff/suites`，共 1958 个小节）**：

| 检查 | 结果 |
| :--- | :--- |
| 小节矩形解析耗时 | 与改动前**持平**（canon 4 行：16.1 ms/行，改动前 15.7–17.0 ms/行） |
| 小节 → 行定位（**不解析 SVG**，只靠 `sync.json`） | 1946 / 1958（99.4%） |
| 小节 → 行定位（该行挂载过之后，用 SVG 里实际出现的小节补齐） | **1958 / 1958** |
| 定位到行后能解析出矩形 | **1958 / 1958**（0 处需要回退逐音符） |
| 逐帧重绘次数 | 每小节 1 次（`tests/test_measure_highlight.py`："同一小节内推进 3 个音符起点"→ 视图只收到 **1 个计划**；改动前是 3 个） |

两个非平凡的情况（都已处理并进测试）：

* **反复记号**：Verovio 把同一小节展开成 `vugbrzt` 与 `vugbrzt-rend2` 两个 ID
  （`sync.json.measures[]` 里两个都在、onset 不同），而 SVG 里只画其中一个。
  映射与查矩形都对 `-rendN` 后缀做双向容忍（canon 一开始有 4 个小节因此退回逐音符，
  修正后 53/53 命中）；
* **整小节只有休止符**：这种小节没有任何音符，`sync.json` 里自然没有"它属于哪一行"，
  未挂载时查不到（four-seasons 有 12 个）。行一旦挂载/解析过，
  视图就把**行内实际出现的小节 ID** 补进映射（`_index_measures()`）——
  播放时当前行必然已挂载，所以实际观感不受影响。

其它验证：

* **真实播放链路**（`tools/diagnose_highlight.py`，canon）：逐帧可见"播放器位置在推进、
  高亮小节在推进（实测走过 `i10rks9u → n11tgru4 → vt94i1u`）、overlay 始终只有 1 个矩形"，
  退出码 0（顺带修掉该工具的老毛病：它比较的是"发声元素**个数**"，
  一直有 2 个音在响就被误判成"没变化"）；
* 页面 JS 用 DOM stub 在 node 里跑通（`tools/check_page_js.js`）：矩形 = `getBBox()` + 外扩、
  切换小节不累积矩形、找不到小节时回退 `setActive(ids)`、舒适区外触发跟随滚动；
* 离屏目视验证（`tools/preview_measure_highlight.py`）：
  `images/measure-highlight.png`（当前小节：小节 421×288px → 高亮框 425×292px）
  与 `images/measure-highlight-far.png`（seek 到未挂载的远行 → 按需挂载 + 补全矩形）。

**顺带简化**：`ScoreView.set_highlight()` 现在返回**实际生效**的计划
（小节矩形可能要在挂载目标行之后才算得出），`PlaybackController._plan` 因此始终反映
真正画出来的内容，`tools/smoke_play.py` 的"高亮矩形有真实尺寸"检查继续有效。

### 12.9 M5 需求变更：源文件「预览」（F3.8，对 v2.5 的改动）

**需求变更**：源文件「选择…」按钮**左侧**增加「预览」按钮；没选文件时不可用，
选了 `musicxml / svg / midi / mp3` 才可用。MusicXML/SVG → 非模态窗口**显示**文件；
MIDI/MP3 → 非模态窗口**播放**，带停止 / 播放按钮。
回归测试见 `tests/test_m5_preview.py`（44 项）、无 Qt 的 URL 路由用例
`tests/test_local_server.py`（5 项），目视验收工具 `tools/check_preview_windows.py`。

**实现**：

| 位置 | 改动 |
| :--- | :--- |
| `ui/tab_convert.py` | 源文件行新增 `btn_preview`（插在「选择…」**左侧**）；`_refresh_preview_button()` 按后缀决定可用性，并给出**原因 tooltip**（未选文件 / 该格式暂不支持）；点击后打开对应窗口（同类型复用一个实例，不堆窗口） |
| `ui/preview.py`（新增） | `preview_kind()` 格式判定 + `ScorePreviewWindow`（乐谱）+ `AudioPreviewWindow`（试听），都是 `QDialog` + `setModal(False)` + `WA_DeleteOnClose=False` |
| `ui/score_host.py`（新增） | 把 `ScoreViewHost`（曲谱视图 + 浮动缩放条）从 `tab_play` 抽出来共用，预览窗口因此免费得到"适应宽度 / 缩放 / 按行懒加载"；`tab_play` 仍 `from .score_host import ScoreViewHost` 再导出，旧导入路径不变 |
| `core/score_render.py` | 新增 `render_preview_svgs()`（只渲染前 `PREVIEW_MAX_SYSTEMS=12` 行、不导出 MIDI/时间轴、不要求"有音符"、强制逐行排版）与 `svg_size()`；新增 `verovio_resource_path()` / `_prepare_toolkit()` |
| `common/paths.py` | 新增 `TMP_DIR`（`.zpy-tmp`）与 `PREVIEW_DIR`（预览缓存）；`.gitignore` 一并忽略 |
| `tests/test_m5_preview.py` | 44 项。其中 `test_preview_cache_layout_is_servable` 守住"预览缓存能被资源服务取到"（见 12.9.1） |

**几个实测结论**（都写进了代码注释与测试）：

1. **`QMediaPlayer` 放不了 `.mid`**：实测 `setSource(canon.mid)` → `MediaStatus.InvalidMedia`
   / `Error.FormatError: Could not open file`；而 `.mp3` 正常（`BufferedMedia`，时长 130.2 s）。
   因此 MIDI 走 **FluidSynth 合成 → 临时 WAV → 播放**（canon 130 s 音乐约 5 s 合成完），
   结果按 `路径 + mtime + size` 缓存到 `.zpy-tmp/preview/audio/`，第二次点预览秒开
   （实测合成出的 WAV 与套件里的 MP3 时长一致：都是 02:10.2）。
2. **路径要 `resolved()`**：`resolve_tools()` 不会自己探测 FluidSynth，
   配置里 `paths.fluidsynth_dir=None` 时必须先 `config.paths.resolved()`
   （否则"未找到 fluidsynth.exe"——开发时踩到过一次，界面会给出这个提示）。
3. **Verovio 在工作线程里会找不到字体资源**（**关键坑**）：只要主线程先渲染过一次，
   此后在工作线程里新建的 toolkit 会在 `loadData()` 阶段失败并打印::

       [Error] Bravura font could not be loaded.
       [Error] The data cannot be loaded because the font resources are not available

   现象是"CLI / 单测全绿，但 GUI 里后台渲染整条链路挂掉"。
   **修正**：每个 toolkit 建好后立刻 `setResourcePath(verovio 自带 data 目录)`
   （`render_score` 与 `render_preview_svgs` 都做了；`AppConfig.paths.verovio_resources`
   这个此前没人用的配置项现在也真正生效）。回归测试：
   `tests/test_m5_preview.py::TestWorkerThreadRender`。
4. **渲染失败要能降级**：选了不是乐谱的 `.xml` 时，窗口切换成**源码文本 + 失败原因**
   （只读等宽），不出现空白窗口 —— "显示文件"的两种理解都能满足。

**窗口参数**：乐谱窗口 1000×640，默认显示前 12 行（canon 这类小曲目会全部显示：
实测 4 行 ≈ 0.2 s，缓存命中后 < 30 ms）；音频窗口 560×170，含播放/暂停、停止、
进度条（可拖动 seek）与时间显示，打开即自动开始播放。

#### 12.9.1 修掉"预览完成 12 行"却"12 行加载失败 HTTP 404"（关键坑）

**现象**（`logs/zpymusic_20261008.log`，Vivaldi / the-four-seasons）：

```text
INFO  zpymusic.ui.preview        曲谱视图后端：QWebEngineView（DOM 高亮）
INFO  zpymusic.ui.preview        渲染 MusicXML 预览：the-four-seasons-complete.mxl
INFO  zpymusic.sync.local_server 资源服务根目录已切换为 ...\.zpy-tmp\preview\score\316cbf895ece
INFO  zpymusic.ui.preview        MusicXML 预览完成：the-four-seasons-complete.mxl，12 行
DEBUG zpymusic.sync.local_server HTTP "GET /svg/sys-0001.svg HTTP/1.1" 404 -
...
WARNING zpymusic.sync.web_view   曲谱页面有 4 行加载失败：
WARNING zpymusic.sync.web_view     行 1  原因=HTTP 404  URL=http://127.0.0.1:11621/svg/sys-0001.svg
```

**根因**：WebEngine 后端取行 SVG 的 URL 约定是 `SystemRef.file` = **相对套件目录**的路径
（套件里是 `svg/sys-0001.svg`）。预览窗口两处都违反了它：

| 违约点 | 旧行为 | 后果 |
| :--- | :--- | :--- |
| 缓存落盘位置 | 行 SVG 平铺在 `<cache>/`（套件是 `<suite>/svg/`） | 请求 `/svg/sys-0001.svg` 会落到 `<cache>/svg/sys-0001.svg`，文件不在那儿 |
| `SystemRef.file` | 只给文件名 `sys-0001.svg`，根目录给 `<cache>` | 原生后端找 `<cache>/sys-0001.svg`（不存在）；WebEngine 请求到根目录下（不存在） |

两个后端因此**整屏都取不到行**（原生后端只在日志里留 `缺少 system SVG：…`，界面是空白）。

**修正**（三处，缺一不可）：

1. `ui/preview.py` 预览缓存落盘到 `<cache>/svg/`（与套件同构）；
2. `SystemRef.file` 改为**相对资源根目录**的路径（`svg/sys-0001.svg`）；
3. `sync/local_server.py` 新增 `/sys/<相对路径>` 路由（页面按它拼 URL，
   逐段 `encodeURIComponent`，保留 `/`）—— `/svg/<名字>` 作为旧路由继续保留，
   `tools/diagnose_asset_server.py` 等既有调用不受影响；越界与扩展名白名单仍由
   `_resolve()` 统一拒绝。

**回归测试**：`tests/test_m5_preview.py::TestScorePreviewWindow::test_preview_cache_layout_is_servable`
（真的起一个 `LocalAssetServer`，按两条 URL 规则各请求一次，断言 200 + `<svg`）与
`tests/test_local_server.py`（`/sys/` 相对路径解析、根目录平铺文件、`..` 越界拒绝、
扩展名白名单、`/svg/` 旧路由不回归）。

> 顺带说明：同一段日志里的 `[Error] Bravura font could not be loaded.` **与本故障无关**，
> 它是 Verovio 在部分平台上会打印的告警（`loadData()` 返回真值、SVG 内容完整），
> 留档在 12.9 §3；排障时不要被它带偏——行 SVG 明明渲染出来了，问题在"取不到文件"。

### 12.10 M5：格式转换（F3.1–F3.7 全量落地）

**目标**：把 §5.4.1 能力矩阵里"本期实现"的转换真正做出来，并补齐 F3.2–F3.7 的界面链路。
实现集中在两个文件：**引擎** `core/convert.py`（不依赖 Qt）与**界面** `ui/tab_convert.py`；
`cli.py convert` 只是引擎的薄壳（GUI 与 CLI 共用同一份实现，避免两套行为）。
回归测试见 `tests/test_m5_convert.py`（77 项），目视验收工具 `tools/check_convert_tab.py`。

**转换形态**（这里有个必须记住的区分）：

* **套件形态**：`musicxml → svg / midi / mp3 / wav` —— 需求 §5.4.1 要求复用生成套件流水线，
  因此产物是**一个套件目录**（含行 SVG + `sync.json` + 音频 + 源 MusicXML）。
  好处：产物能直接「载入播放界面」看谱跟读（F3.6）。
* **单文件形态**：其余转换（`musicxml → pdf`、`midi → musicxml`、`midi → mp3/wav`、
  `mp3 ↔ wav`、`svg → musicxml`、`pdf → svg`）产物就是目标目录下的一个文件。

**模块分工**：

| 位置 | 职责 |
| :--- | :--- |
| `core/convert.py` | 能力矩阵（原在 `cli.py`）、格式判定、依赖自检（`check_environment` / `requirements_for`）、命名模板与重名策略、`convert_one()` 主入口、各转换实现、`iter_convertible()` |
| `ui/tab_convert.py` | 批量列表、转换设置、依赖自检面板、保真度提示、后台批量执行（`BatchRunner`）、结果区按钮 |
| `ui/open_utils.py` | 「打开所在文件夹」/「打开产物」（从 `main_window` 抽出，两处共用） |
| `ui/main_window.py` | 连接 `suite_ready`（转换产物 → 切到播放页并载入）与 `settings_requested`（依赖缺失 → 弹设置） |
| `common/config.py` | 新增 `ui.skip_fidelity_prompts`（F3.3 的"不再提示"）与 `ui.convert_name_template`（F3.7） |

**实测数据（canon-in-d-easy，本机 2026-02）**：

| 转换 | 耗时 | 产物 |
| :--- | ---: | :--- |
| `musicxml → midi` | 0.2 s | 套件目录 + `canon-in-d-easy.mid` |
| `musicxml → svg` | 0.1 s | 套件目录 + 4 个行 SVG + `sync.json` |
| `musicxml → pdf` | 0.7 s | 60 KB PDF（MuseScore 排版） |
| `musicxml → wav` | 4.9 s | 22 MB WAV（FluidSynth 27× 实时） |
| `musicxml → mp3` | 6.0 s | 3.0 MB MP3 |
| `midi → musicxml` | 0.5 s | 131 KB MusicXML（2 声部，可被本工具回读） |
| `midi → wav` | 4.8 s | 22 MB WAV |
| `mp3 → wav` | 0.1 s | ffmpeg 转码 |
| `svg → musicxml` | < 0.1 s | 复用同套件保留的 MusicXML（带显式警告） |
| `pdf → svg` | — | 缺 pypdf → 明确拒绝并给出安装提示 |

**本次定位到的两个坑**（都写进了测试）：

1. **"套件形态"不能只看目标格式**。最初用 `target in {svg,midi,mp3,wav}` 判断，
   于是 `midi → wav` 也被当成套件，输出目录被当成套件目录，最后
   `PermissionError: [WinError 5]`（`Path.replace` 到目录上）。
   修正：`is_suite_form(sfmt, tfmt)` 要求**源必须是 musicxml**。
2. **调 MuseScore 必须清掉 `QT_QPA_PLATFORM`**。批量转换跑在 `QThread` 里，
   子进程继承了我们为 WebEngine 设的 Qt 环境变量；实测
   `QT_QPA_PLATFORM=offscreen` 会让 MuseScore 4 直接崩溃
   （`returncode=0xC0000409`，栈保护终止），而干净环境下同一命令 0.9 s 就成功。
   修正：`_child_env()` 在启动 MuseScore 前删掉 `QT_QPA_PLATFORM` /
   `QT_PLUGIN_PATH` 等变量（回归测试 `test_musicxml_to_pdf_survives_offscreen_qt`
   故意把 offscreen 塞进环境变量，要求转换**仍然成功**）。

**已知限制**（界面里都会明说，不静默降级）：

* `svg → musicxml` 只对本工具生成的 SVG 且能找到配套 MusicXML 时可用；
* `pdf → svg` 依赖 `pypdf` 提取内嵌乐谱，不做 OMR、不做位图降级；
* `midi → wav/mp3` 的音色取决于 MIDI 内的 Program Change 与音色库，缺 Program 时会给出警告；
* 取消是协作式的：外部进程（MuseScore / FluidSynth / ffmpeg）无法中途打断，
  取消后当前文件会跑完，剩余文件标记为"已取消"；
* **多种源格式混在一个列表里**时目标格式取交集（如 musicxml + midi 只剩 mp3/wav）；
  交集为空（四种格式混排）时**放开全部目标**并在依赖自检里逐个标出
  "哪个文件不支持当前目标" + 提示分批转换 —— 否则用户会卡在"一个目标都点不动"的死角
  （`tools/check_convert_tab.py` 与 `test_no_common_target_is_not_a_dead_end` 都在盯这一点）。

### 12.11 M6：浏览器引擎的 GPU 环境修正（对 v2.7 的改动）

**起因**：README 的"排障：浏览器引擎的 GPU 报错"把

```text
ERROR:gpu_channel_manager.cc(956) Failed to create GLES3 context, fallback to GLES2.
ERROR:gpu_channel_manager.cc(967) ContextResult::kFatalFailure:
                                  Failed to create shared context for virtualization.
```

归因于"机器没有独显/核显驱动异常、远程桌面、虚拟机、GPU 驱动被策略禁用"，
并把"程序自动设置软件渲染"当成修复。而用户报告：**本机 GPU 与驱动都正常，
其他项目（`pyqt6-tutorial` 的 `webview.py` / `browser.py`）用 `QWebEngineView` 从不报错。**

**对照实验**（同一台机器、同一解释器，PySide6 6.9.3 / Qt 6.9.3，加载同一套件前 6 行 SVG）：

| 实验 | 环境 | GPU 报错 | 谱面 |
| :--- | :--- | :--- | :--- |
| 基线 | 不设任何 GL / WebEngine 变量（等同 pyqt6-tutorial 的 demo） | 无 | 正常 |
| 旧默认 | `prepare_webengine_env()` 原样 | **有** | 正常（非致命） |
| 单项 | 只加 `QT_QUICK_BACKEND=software` | **有** | 正常 |
| 单项 | 只加 `QT_OPENGL=software` | 无 | 正常 |
| 单项 | 只加 `--disable-gpu` | **有** | 正常 |
| 单项 | 只加 `--disable-gpu-compositing` / `-rasterization` / `-accelerated-2d-canvas` / `-accelerated-video-decode` / `--disable-dev-shm-usage` / `--no-sandbox` / `QTWEBENGINE_DISABLE_SANDBOX=1` | 无 | 正常 |
| 真实曲谱页 | 干净环境（`LocalAssetServer` + 6 张 `sys-*.svg`） | 无 | **6/6 张渲染成功** |

另外实测本机 `QOpenGLContext` 可建出 **OpenGL 4.6 Compatibility** 上下文。

**结论**：那两条报错是**程序自己关掉 GPU 造成的、且非致命**；
真正让 WebEngine "用不起来"的是 `named-platform-channel-pipe ... 拒绝访问 (0x5)`，
它只在**受限沙箱 / 受管环境**（进程不允许创建命名管道）里出现 —— 日志里
17:27:57 那次探测失败就是它，两分钟后同一台机器上 WebEngine 又跑通了
（`WebEngine 后端就绪` + SVG `HTTP 200`）。

**改动**：

| 文件 | 改动 |
| :--- | :--- |
| `sync/web_view.py` | `_DEFAULT_FLAGS` 只剩 `--disable-dev-shm-usage`（不再关 GPU）；新增 `_SOFTWARE_FLAGS` 作为**兜底 profile**；`_SOFTWARE_QT_ENV` 只在兜底 profile 生效；`is_webengine_available()` 改为**两段式**探测（默认 → 仅 GL 失败才软件渲染），并用 `classify_probe_failure()` 把失败分成 管道 / GL / 超时 / 其它；日志补打 `QT_QUICK_BACKEND` 与 `profile` |
| `ui/main_window.py` | `run_gui()` 不再无条件调用 `prepare_webengine_env()`（该调用会把软件渲染变量泄漏给**所有子进程**，与 §12.10 的 MuseScore 崩溃同类） |
| `common/config.py` | `UiConfig.score_backend` 默认 `native` → `auto` |
| `ui/settings_dialog.py`、`ui/preview.py` | 文案与默认值同步 |
| `tools/diagnose_web_switch.py` | 修复过期调用（`view._run_js(js, cb)` 早已只接受一个参数 → 必然 `TypeError`）、补 `show()`（否则视口为 0，IntersectionObserver 不触发，看起来"全部加载失败"）、输出探测分类 |
| `tools/probes/probe_webengine_perf.py` | **新增**：默认(GPU) vs 软件渲染兜底的对比基准，含"默认 profile 不得出现 GPU 报错"的 PASS/FAIL 判定 |
| `tests/test_webengine_env.py` | **新增 17 项**：默认 profile 不含 `--disable-gpu`/`--no-sandbox`、兜底不含 `--disable-software-rasterizer`、默认注入不碰 `QT_OPENGL`/`QT_QUICK_BACKEND`、用户环境变量被尊重、失败分类、两段式探测（GL 才降级、管道不降级）、`run_gui()` 不再注入 |

**实测验收**（`the-four-seasons-complete` 60 行，1200×900）：

| profile | page_ms | first_ms | scroll_ms | mounted | svg | err | GPU 报错 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| default（GPU） | 180.0 | 66.2 | 22376.7 | 60/60 | 60 | 0 | **无** |
| software（兜底） | 150.5 | 66.5 | 21945.1 | 60/60 | 60 | 0 | 有（对照组，符合预期） |

**注意**：两个 profile 的 `scroll_ms` 基本一致（比值 ≈ 1.0）—— 这条路径的耗时由
"取回 + 解析 + 栅格化 60 张 SVG"主导，两者都在 CPU 侧。所以本次修正的意义**不是**
"GPU 更快"，而是：① 不再发那两条会误导排障方向的报错；② 不再无谓地关掉 GPU 合成；
③ 软件渲染只在真正需要时启用；④ 不再污染子进程环境。基准的作用是守住 ①③④。

### 12.12 M6：补齐 WebEngine 后端的缩放 / 适应宽度（对 v2.8 的改动）

**起因**：§12.11 把默认后端改成 `auto` 之后，用户立刻反馈
「同步播放」页的**「适应宽度」失效、默认缩放 100%**。

**根因**：不是缩放算法坏了，而是**两个后端的接口不一致**。

`ui/score_host.py` 是用 `hasattr` 探测后端能力的，缺方法就**静默降级**：

```python
self.zoom_bar = ZoomBar(self, fit_default=getattr(score_view, "_fit_width", True))
if hasattr(score_view, "set_zoom"): ...        # WebScoreView 有 → 工具条被认为可用
def _apply_fit_width(self, on):
    if not hasattr(self.score_view, "set_fit_width"):
        return                                  # ← WebScoreView 没有 → 直接返回，什么都不做
```

而 `WebScoreView` 当时**只有** `set_zoom` / `zoom`，缺：

| 缺失成员 | 后果 |
| :--- | :--- |
| `set_fit_width` | 「适应宽度」按钮点了没反应（`_apply_fit_width` 直接 return） |
| `fit_width` / `fit_width_enabled` | 无法计算，也无从查询状态 |
| `zoom_changed` 信号 | 百分比数字永远停在旧值 |
| `_fit_width` 属性 | 宿主 `getattr(..., True)` 拿到默认 `True` → **按钮显示为已勾选**，看起来"开了"，实际从未生效 |

组合起来就是用户看到的现象：**按钮勾着、数字 100%、谱面横向滚动**。

**为什么以前没发现**：默认后端是 `native`，走的是 `NativeScoreView`（接口齐全）；
WebEngine 分支除了"能不能启动"之外从没被真正用过，「适应宽度」这条链自然也没人验证过。
**换默认后端 = 换实现，接口差异会在这一刻全部暴露。**

**改动**：

| 位置 | 改动 |
| :--- | :--- |
| `sync/web_view.py` | 补齐 `zoom_changed` 信号、`set_fit_width` / `fit_width` / `fit_width_enabled`、`_fit_width` / `_fit_view_size` 状态；用**事件过滤器**（本类不是 QWidget，收不到 `resizeEvent`）在宿主 Resize / Show 时重算；`load()` 与 `_on_load_finished()` 各补一次适应宽度；`clamp_zoom()` 与原生后端**共用** `MIN_ZOOM`/`MAX_ZOOM`（30%–400%） |
| `sync/web_view.py` | 抽出纯函数 `content_width_of()` / `fit_zoom_for()`，让"适应宽度"的输入与算式能在**不启动 Chromium** 的 CI 里单测 |
| `sync/local_server.py` | `#systems` 的 `align-items:center` → `safe center`（保留 `center` 作回退）：谱面比视口宽时，普通 center 会把溢出平均分到两侧，左侧一截被裁掉且滚不回来 |
| `tests/test_webengine_env.py` | 新增 25 项：`REQUIRED_VIEW_API` 逐名对照两个后端、`_fit_width` 默认值一致、缩放区间共用、适应宽度算式、页面 CSS 护栏；另在 `tests/test_m6_view_fixes.py` 增补 1 项**不锁定后端**的端到端首屏用例，以及 `tests/conftest.py` 的"测试不得改写 `config.json`"护栏（`MainWindow.closeEvent()` 会 `config.save()`，会把测试里钉的后端持久化） |

**回归测试口径**：总计 **364 项**（本次 +26），并且在**两种环境各跑一遍都必须通过** ——
① 受限沙箱（Chromium 起不来 → `auto` 回退原生）；② 可启动 Chromium（走 `WebScoreView`）。
新加的端到端用例刻意不锁定后端，只断言两个后端都必须满足的不变量，
否则"默认后端是 `auto`"这件事会让测试随环境摇摆（本次就撞到过一次）。

**实测**（2100px 谱面，宿主 1200×900）：

| 场景 | NativeScoreView | WebScoreView |
| :--- | :--- | :--- |
| 首屏（默认适应宽度） | zoom 0.5581 / 显示 56% / 勾选 ✔ | zoom 0.5562 / 显示 56% / 勾选 ✔ |
| 手动 100% | zoom 1.0 / 自动取消勾选 | zoom 1.0 / 自动取消勾选 |
| 重新勾选适应宽度 | zoom 0.5581 / 56% | zoom 0.5562 / 56% |
| 窗口缩到 800 宽 | zoom 0.3676 / 37% | zoom 0.3657 / 37% |

页面侧（Chromium 内实测）：

| 状态 | `window.innerWidth` | `documentElement.scrollWidth` | `.sys` 的 `left` |
| :--- | ---: | ---: | ---: |
| 适应宽度（0.556） | 2157 | 2127 | 14（居中，无横向滚动条） |
| 100%（修 CSS 前） | 1200 | **1642** | **-458（左侧 458px 滚不回来）** |
| 100%（修 CSS 后） | 1200 | **2100** | **0（可完整滚动）** |

---

## 附录 A：环境实测记录

| 检查项 | 命令 / 方法 | 结果 |
| :--- | :--- | :--- |
| Python 版本 | `C:\miniconda3\envs\ibase\python.exe --version` | `Python 3.13.16` |
| conda 环境 | 枚举 `C:\miniconda3\envs` | 仅 `ibase` |
| 已装关键包 | `pip list` | `PySide6 6.9.3`、`PySide6-Addons 6.9.3`、`numpy 2.5.3`、`scipy 1.18.1`、`mido 1.3.3`（**尚无 verovio / music21 / pyfluidsynth**） |
| Verovio 可用性 | `pip index versions verovio`；`pip download verovio --only-binary=:all:` | PyPI 最新 `6.3.0`；解析到 **`verovio-6.3.0-cp310-abi3-win_amd64.whl`**（`abi3` → 兼容 CPython 3.13，Windows 有预编译包，无需编译） |
| pyfluidsynth 可用性 | `pip index versions pyfluidsynth` | 最新 `1.4.0`（纯 Python `py3-none-any`，运行时需 `libfluidsynth` 动态库 → 对应风险 R1） |
| MuseScore 4 | `Test-Path`；`MuseScore4.exe --help` | 存在：`C:\Program Files\MuseScore 4\bin\MuseScore4.exe`，CLI 可用（支持 `-o`、`-T/--trim-image`） |
| ffmpeg | `C:\ffmpeg\bin\ffmpeg.exe -version` | `ffmpeg version N-122544-g8966101fa6-20260125` |
| MP3 编码器 | `ffmpeg -encoders` | 含 `libmp3lame`（MP3 / MPEG audio layer 3）→ **无需额外编码器** |
| 样本格式 | 读文件头 + 解析 `META-INF/container.xml` | 10 个样本全部为 **ZIP（`.mxl`）**；根文件名为 `score.xml` 或 `lg-*.xml` → 解析必须走容器规范（§5.1.3） |
| 音色库授权 | 读 `sound/MS Basic_License.md` | MuseScore_General 系列为 **MIT**；FluidR3 系列需按原包说明标注来源 |

> 备注：本次验证中，`pip download` 在沙箱下解包元数据时被文件策略拒绝（`PermissionError` on `pip-unpack-*/…whl.metadata`），因此**未实际安装依赖**；但包名、版本与 Windows 轮子标签均已从 PyPI 元数据确认。开工第一步应在 `ibase` 环境中执行 `pip install verovio music21 pyfluidsynth` 并跑通一个最小渲染脚本。

## 附录 B：关键 API 速查（Verovio，用于 §5.2 / §5.4）

| API | 用途 |
| :--- | :--- |
| `toolkit.loadFile(path)` / `loadZipDataBuffer(bytes)` | 加载 MusicXML（含 `.mxl`；`loadFile` 内部会解压） |
| `toolkit.setOptions({...})` | `scale`、`pageWidth`、`pageHeight`、`svgAdditionalAttribute: ["note@pname","note@oct"]` 等 |
| `toolkit.getPageCount()` | 页数 |
| `toolkit.renderToSVG(pageNo)` / `renderToSVGFile(path, pageNo)` | 取 / 写 SVG |
| `toolkit.renderToMIDI()` / `renderToMIDIFile(path)` | 导出 MIDI（**必须先于时间查询调用**） |
| `toolkit.renderToTimemap('{"includeMeasures": true, "includeRests": false}')` | 导出时间轴 |
| `toolkit.getTimesForElement(id)` | ⚠ **本机 verovio 6.3.0 实测不可靠**：对绝大多数 ID 返回全 0，且逐个调用不 scale（29324 个 ID 耗时 **132 s**）。**不要用它构建全曲时间轴**，改用 `renderToTimemap`（见下） |
| `toolkit.renderToTimemap(json)` | ✅ **时间轴的正确来源**。返回 `list[dict]`（不是 JSON 字符串），字段：`tstamp`(**毫秒**)、`qstamp`(四分音符数)、`measureOn`(小节 ID)、`on[]`/`off[]`(元素 ID)、`tempo`(仅出现处有)。单遍配对 `on`/`off` 即可得到 onset/duration，全曲耗时 < 0.2 s |
| `toolkit.getNotatedIdForElement(id)` | 把展开 ID `<base>-rendN` 映射回原始 `xml:id`（处理连音/反复幽灵音符的关键） |
| `toolkit.getElementsAtTime(ms)` | 某毫秒时刻正在播放的元素 ID（播放高亮可直接用） |
| `toolkit.getMIDIValuesForElement(id)` | 元素的 MIDI 参数（需先调 `renderToMIDI`） |
| `toolkit.getElementAttr(id)` | 元素全部 MEI 属性（含 Verovio 未使用的） |
| `toolkit.getPageWithElement(id)` | 元素所在页（多页时用于自动翻页） |
| SVG 结构约定 | MEI `<note xml:id="n1">` → SVG `<g class="note" id="n1">`；`@type` 追加为额外 class。实测 SVG 片段：`<g id="x1wy5da5" class="note" data-pname="d" data-oct="3" data-dur="8">` |
| 实测注意 1 | `pageHeight` 上限为 **60000**（传 200000 会报 out of bounds 并被忽略） |
| 实测注意 2 | `loadFile()` 能直接读 `.mxl`（内部解压）；但本项目仍自己解容器，以便拿到 `container.xml` 里的真实根文件名与原始字节 |
| 实测注意 3 | `renderToMIDI()` 不返回字符串时（老版本）需自行 `json.loads`；本机返回 base64 字符串 |
| 实测注意 4 | 渲染选项经 `setOptions()` 设置，**必须在 `loadData()` 之前**，否则不生效 |

## 附录 C：参考资料

- Verovio 参考书 · Toolkit 方法（`getTimesForElement` / `getElementsAtTime` / `renderToTimemap` / `loadZipDataBuffer` 等）：<https://book.verovio.org/toolkit-reference/toolkit-methods.html>
- Verovio 参考书 · CSS 与 SVG（结构保留、`g.note` 选择器、`svgAdditionalAttribute`）：<https://book.verovio.org/interactive-notation/css-and-svg.html>
- FluidSynth 手册（`-F/--fast-render`、`-g/--gain`、`-R/--reverb`、`-C/--chorus`、`-K/--midi-channels`、`-r/--sample-rate`）：<https://manpages.debian.org/experimental/fluidsynth/fluidsynth.1>
- 项目内前置调研（技术栈对比、MusicXML ↔ MIDI 有损性、MuseScore 高亮机制）：`docs/predo.md`
- MuseScore 4 命令行导出（`-o` 按扩展名决定格式）：<https://handbook.musescore.org/>

## 附录 D：Verovio 实测数据（M0 探针）

环境：verovio 6.3.0 / Python 3.13.16 / Windows，样本取自 `staff/musicxml/`。

### D.1 timemap 结构

```
total entries: 371（canon）
field histogram: measureOn=53, on=369, qstamp=371, tempo=1, tstamp=371, off=367
first: {"measureOn": "bu2cbez", "on": ["v1904n71"], "qstamp": 0, "tempo": 100, "tstamp": 0}
next : {"off": ["v1904n71"], "on": ["y1tn4xbn"], "qstamp": 0.5, "tstamp": 300}
last : {"off": ["wihv2ba","y1xpueoe"], "qstamp": 212, "tstamp": 127200}
```

* `tstamp` 单位是**毫秒**：`qstamp` 212 对应 `tstamp` 127200 → 600 ms/四分音符 → **100 BPM**，
  与文件里的 tempo 记号一致（早期误判为 MIDI tick，已纠正）。
* `on[]` 长度分布（canon）：1 元 275、2 元 86、3 元 8、空 2 → 和弦最少 2 个元素同时 `on`。
* 与 `getElementsAtTime(ms)` 交叉验证：4/4 抽样时刻一致。

### D.2 规模与耗时（关键：不要调用 getTimesForElement）

| 样本 | system 数 | timemap 条数 | 音符元素 | 时长 | loadFile | renderToMIDI | renderToTimemap | 配对构建 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| canon-in-d-easy | 11 | 371 | 471 | 127.2 s | 0.06 s | 0.02 s | 0.01 s | <0.01 s |
| bach-cello-suite-no-1 | 15 | 658 | 660 | 140.0 s | 0.07 s | 0.02 s | 0.01 s | <0.01 s |
| waltz-in-a-minorchopin | 10 | 427 | 920 | 120.5 s | 0.05 s | 0.03 s | 0.01 s | 0.01 s |
| rachmaninoff-…-solo-piano | 12 | 437 | 1215 | 126.5 s | 0.13 s | 0.06 s | 0.01 s | 0.01 s |
| vivaldi-…-winter-for-solo-piano | 62 | 2482 | 5698 | 479.9 s | 0.61 s | 0.23 s | 0.03 s | <0.01 s |
| the-four-seasons-complete | **276** | 10469 | **29324** | 2278.6 s | 3.74 s | 1.20 s | 0.16 s | **0.17 s** |

对照：同一 four-seasons 若逐个调用 `getTimesForElement` 取 29324 个 ID → **131.92 s**
（约 730 倍），且返回值基本全 0 → **该 API 在本版本不可用**。

### D.3 版面选项对比（3 个样本）

| 选项 | canon | waltz | four-seasons |
| :--- | :--- | :--- | :--- |
| `breaks=smart`（默认尺寸） | 2 页 840×1188 | 2 页 840×1188 | 98 页 840×1188，≈24 MB |
| `breaks=none` + `pageHeight=20000` | 1 页 7145×191 | 1 页 6316×234 | 1 页 167972×568，23 MB，首行渲染 16.4 s |
| **`breaks=smart` + `pageHeight=400` + `adjustPageHeight`** | **11 页 840×186** | **9 页 840×1632→紧凑** | **276 页 840×406，≈27 MB，20 行渲染 0.33 s** |

### D.4 音频渲染实测

| 项目 | 结果 |
| :--- | :--- |
| 命令 | `fluidsynth.exe -ni -g 0.6 -r 44100 -a file -O s16 -T wav -F <out.wav> <sf> <mid>` |
| canon（130.2 s 音频，MuseScore_General.sf3） | **4.83 s**（≈27× 实时） |
| four-seasons（2278 s 乐谱） | 批量 10 个套件总耗时 **252 s** |
| MIDI 时长 vs `sync.json` 时间轴 | 10 个样本误差均 **< 10 ms**（同源验证通过） |
| 渲染音频 vs 乐谱时长 | tango +3.1 s（8.4%）、ragtime +8.9 s（6.0%）→ 均为混响/延音尾巴，**正常** |
| ffmpeg `libmp3lame` | 可用，默认 192 kbps → canon 输出 3.13 MB |

### D.5 M4 版面与渲染实测

**版面（canon / four-seasons，`breaks=smart`）**

| 配置 | canon | four-seasons | 结论 |
| :--- | :--- | :--- | :--- |
| `scale=40, pageHeight=400, adjustPageHeight=True`（M1 旧值） | 11 行 840×188 | 276 行 840×406 | 符头仅 2.3 px，字太小 |
| **`scale=100, pageWidth=2100, pageHeight=1500, adjustPageHeight=True`（v2.3 现值）** | **4 行 2100×1260（贴合）** | **276 行 2100×~1439** | ✅ 采纳；符头 22.7 px |

> **注**：M4 中期版本曾用 `scale=100, pageHeight=1500, adjustPageHeight=False`，
> 结果是每个 system 固定 2100×1500，**内容下方留约 400–1000 px 空白**，
> 且 2100 px 宽的页面在窄窗口里必然出现横向滚动条 —— 这正是用户报告的
> "界面横向扩展很大、不能缩小、两侧大量留白"。v2.3 已改为开启
> `adjustPageHeight`（高度贴合）+ 视图**默认按宽度自适应**。

**几何解析（`svg_geometry.parse_svg`）**

| 项目 | 实测值 |
| :--- | :--- |
| 单位换算系数 | 840 / 21000 = **0.04**（由内层 viewBox 决定） |
| 符头包围盒 | **9.08 × 7.7 px** |
| 5 线谱表高度 | **28.8 px**（线距 5.76 px，比例正确） |
| 相邻音符间距 | 11–21 px |
| note 元素解析覆盖率 | **100%**（canon 32/32、four-seasons 110/110、167/167） |
| 单 system 解析耗时 | canon **9 ms** / four-seasons **22 ms** |

**QtSvg 渲染对比（同一文件、同一 QSvgRenderer）**

| 输入 | `isValid()` | `defaultSize()` | 深色像素 |
| :--- | :--- | :--- | :--- |
| 原始嵌套结构 | True | 840×188 | **0（全白）** |
| `flatten_svg()` 之后 | True | 840×188 | **4501** |

**懒加载性能（`NativeScoreView`，four-seasons 276 行）**

| 操作 | 耗时 |
| :--- | :--- |
| 载入套件（仅登记 system，不挂载 SVG） | **79 ms** |
| 首屏挂载（视口附近 3–6 行） | 0.1 ms |
| 单个 system 挂载（解析几何 + 建渲染器 + 加 item） | 9–22 ms |
| 连续滚动并挂载 60 行 | 1.17 s（约 20 ms/行） |

## 附录 E：可复现验证命令

```powershell
$env:PYTHONPATH = "C:\MyCodes\py-space\zpymusic\src"
$py = "C:\miniconda3\envs\ibase\python.exe"

# 1) 环境自检
& $py -m zpymusic probe

# 2) 查看源文件（声部 / MIDI 通道 / GM 音色）
& $py -m zpymusic inspect "zpymusic\staff\musicxml\canon-in-d-easy.mxl"

# 3) 生成单个套件（覆盖已存在）
& $py -m zpymusic generate "zpymusic\staff\musicxml\canon-in-d-easy.mxl" --overwrite=overwrite

# 4) 全量生成 10 个样本 + 完整性校验
& $py -m zpymusic generate "C:\MyCodes\py-space\zpymusic\staff\musicxml" --overwrite=overwrite -q
& $py -m zpymusic validate "C:\MyCodes\py-space\zpymusic\staff\suites"

# 4b) 格式转换（M5：与 GUI 共用引擎，示例覆盖单文件与套件两种形态）
& $py -m zpymusic convert "staff\musicxml\canon-in-d-easy.mxl" -t pdf -o .zpy-tmp\conv
& $py -m zpymusic convert "staff\musicxml\canon-in-d-easy.mxl" -t mp3 -o .zpy-tmp\conv --name-template "{base}_{format}"
& $py -m zpymusic convert "staff\suites\canon-in-d-easy\canon-in-d-easy.mid" -t musicxml -o .zpy-tmp\conv --json
& $py -m zpymusic convert "staff\suites\canon-in-d-easy\canon-in-d-easy.mp3" -t wav -o .zpy-tmp\conv

# 5) 单元测试（全套 321 项：M5 转换 77 + M5 预览 44 + M7 小节高亮 31）与冒烟测试
Set-Location "C:\MyCodes\py-space\zpymusic"
& $py -m pytest -q
& $py -m pytest tests/test_m5_convert.py -q               # 只跑"格式转换"的回归测试
& $py -m pytest tests/test_m5_preview.py -q               # 只跑"源文件预览"的回归测试
& $py -m pytest tests/test_measure_highlight.py -q        # 只跑"跟随小节"的回归测试
& $py tools\smoke_play.py canon-in-d-easy                 # 播放页端到端（离屏）
& $py tools\check_convert_tab.py canon-in-d-easy          # 转换页目视验收（截图）
& $py tools\check_preview_windows.py canon-in-d-easy      # 预览窗口目视验收（截图）
$env:QT_QPA_PLATFORM = "offscreen"; & $py tools\check_gui_launch.py

# 6) 套件交叉验收（ID 一致性 / MIDI 同源 / 音频未截断 / 高亮起点匹配）
& $py tools\verify_suites.py        # 可加参数只验证部分，如：verify_suites.py canon

# 7) 启动图形界面
& $py -m zpymusic.gui
```

### E.1 M1 验收实测结果（2026-02，10 个样本）

`tools/verify_suites.py` 输出（耗时列已省略）：

```
套件                                    元素   事件  SVG  ID一致  MIDI-Δt   音频-Δt  note-on匹配
bach-cello-suite-no-1-for-violin         660    660   15    OK      7ms   +2602ms       100%
bach-minuet-in-g-major-bwv-anh-116       311    622    7    OK      4ms   +3077ms       100%
canon-in-d-easy                          442    471   11    OK      5ms   +3018ms       100%
i-wanna-be-like-you-ragtime-...         1754   1754   36    OK      2ms   +8886ms       100%
maple-leaf-rag-scott-joplin             1581   2464   15    OK      5ms   +3131ms       100%
rachmaninoff-...-solo-piano             1215   1215   12    OK      8ms   +3125ms       100%
tango-la-cumparsita-...-parte-a          153    153    4    OK      5ms   +3087ms       100%
the-four-seasons-complete              29324  29324  276    OK      7ms   +3215ms       100%
vivaldi-...-winter-for-solo-piano       5698   5698   62    OK     16ms   +5162ms       100%
waltz-in-a-minorchopin                   627    920   10    OK      4ms   +3019ms       100%
```

四项验收结论：

1. **ID 一致性 100%** —— 43281 个时间轴事件引用的元素 ID **全部**能在对应 SVG 中找到
   （对应 §8.2 验收标准 2）。这是 M4 高亮能工作的前提。
2. **MIDI 与时间轴同源** —— 10 个样本误差 **2–16 ms**（阈值 100 ms），
   证实 D2/D3 的"同一次 Verovio 渲染导出"策略有效。
3. **音频未被截断** —— 音频时长恒比乐谱长 2.6–8.9 s，全部是 FluidSynth 的混响 / 延音尾巴。
4. **高亮起点与 MIDI note-on 100% 对齐** —— 把 `sync.json` 的事件起点序列与从 MIDI
   读出的 note-on 绝对时间序列比对（容差 ±100 ms），**10 个样本全部 100% 命中**
   （对应 §8.2 验收标准 3 的自动化版本）。这条最直接地证明"播放时高亮会落在正确的音符上"。

**M0–M2 总验收结果**：10 个样本全部生成成功；47 项单元测试通过；GUI 冒烟 17 项检查通过；
套件交叉验收 4 项指标全部通过。

> **写验收脚本时踩到的两个坑（值得记下）**：
> 1. MIDI 格式 1 下，各轨道的时间是**各自从 0 开始的增量 tick**，必须先按绝对 tick
>    合并所有轨道再按 tempo 换算成秒；若逐轨道独立累加，会把各轨时长相加
>    （canon 会算出 212 s 而非正确的 127.2 s）。
> 2. `mido.MidiFile.play()` 是**按真实时间**产出事件的生成器，用于离线校验会真的阻塞
>    整整一首曲子的时长（实测 127 s）。
>
> `tools/verify_suites.py` 已按正确方式实现，并在代码注释里写明这两点。
